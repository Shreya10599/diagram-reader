import base64
import json
import logging
import os
import re
from typing import Literal, Optional

from anthropic import Anthropic
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()

logger = logging.getLogger("diagram_reader")
logging.basicConfig(level=logging.INFO)

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

app = FastAPI(title="Diagram Reader API")

# Dev CORS: Vite serves the front-end on 5173. Tighten this before deploying.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_client: Optional[Anthropic] = None


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


@app.get("/health")
async def healthcheck():
    return {"status": "ok"}


# ---- Request / response models -------------------------------------------------
# Must match the shape documented in packages/front-end/src/mockApi.js exactly,
# or the front-end needs no changes when this replaces the mock.


class ChartTask(BaseModel):
    """Optional targeted-computation request, layered on top of the normal
    full extraction. Covers cases like LIHEAP ("compute average monthly
    usage from this bill chart") and inherited-stock valuation ("find the
    high/low for this specific date and average them") with one mechanism
    instead of a bespoke endpoint per use case.

    Example (LIHEAP-style summary):
        {"type": "summary", "instruction": "Compute the average monthly
         usage across all months shown"}

    Example (stock-style lookup, not implemented in the prompt yet):
        {"type": "lookup", "instruction": "Find the high and low price for
         this date and average them", "target": "2019-06-15"}
    """

    type: Literal["summary", "lookup", "full_extraction"] = "full_extraction"
    instruction: Optional[str] = None
    target: Optional[str] = None  # e.g. a specific date, for lookup tasks


class AnalyzeChartRequest(BaseModel):
    image: str  # data URL, e.g. "data:image/png;base64,...."
    task: Optional[ChartTask] = None


class SeriesPoint(BaseModel):
    label: str
    value: float


class StructuredData(BaseModel):
    chartType: Literal["bar", "line", "pie", "scatter", "other"]
    title: str
    xLabel: Optional[str] = None
    yLabel: Optional[str] = None
    unit: Optional[str] = ""
    series: list[SeriesPoint]
    # Not consumed by the front-end yet — reserved for the verification-pass /
    # confidence UI (draggable-marker correction) planned as a follow-up.
    confidence: Optional[Literal["high", "medium", "low"]] = None
    uncertainValues: Optional[list[str]] = None


class ComputedAnswer(BaseModel):
    """The answer to a specific ChartTask — a derived figure the user
    actually needs (e.g. LIHEAP's average monthly usage), not just the raw
    table. `formula`/`sourcePoints` exist so the user can see exactly how
    the number was derived and verify it before copying it anywhere —
    same "show your work" principle as the worksheet-not-autofill design."""

    label: str
    value: float
    unit: Optional[str] = ""
    formula: str
    sourcePoints: list[str] = []
    confidence: Optional[Literal["high", "medium", "low"]] = None


class AnalyzeChartResponse(BaseModel):
    description: str
    shortDescription: str
    structuredData: StructuredData
    computedAnswer: Optional[ComputedAnswer] = None


# ---- Extraction prompt -----------------------------------------------------------
# Encodes the axis-calibration + self-check procedure discussed in planning:
# anchor to labeled gridlines before reading values, rather than estimating
# proportionally from bar/slice size by eye. This is a single-pass version;
# a second, separate verification call (compare extracted table back against
# the image) is the next step, not yet implemented here.

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


@app.post("/analyze-chart", response_model=AnalyzeChartResponse)
async def analyze_chart(payload: AnalyzeChartRequest):
    media_type, base64_data = _parse_image_data_url(payload.image)
    client = get_client()

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
                            "text": _build_user_instruction(payload.task),
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
        logger.exception("Claude API call failed")
        raise HTTPException(status_code=502, detail=f"Claude API call failed: {exc}")

    if response.stop_reason == "max_tokens":
        # Diagnose truncation explicitly instead of letting it surface as a
        # confusing "couldn't be parsed as JSON" error further down — this
        # is exactly what happened when effort="max" started spending more
        # of the budget on thinking, leaving the JSON cut off mid-output.
        logger.error("Response hit max_tokens (likely truncated mid-JSON): %s", response)
        raise HTTPException(
            status_code=502,
            detail="Claude's response was cut off (hit max_tokens) before finishing — "
            "usually means max_tokens needs to be raised for the current effort level.",
        )

    raw_text = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    )
    parsed = _extract_json(raw_text)

    try:
        return AnalyzeChartResponse(**parsed)
    except Exception as exc:
        logger.error("Claude JSON didn't match expected schema: %s\nParsed: %s", exc, parsed)
        raise HTTPException(
            status_code=502,
            detail=f"Claude's response didn't match the expected schema: {exc}",
        )
