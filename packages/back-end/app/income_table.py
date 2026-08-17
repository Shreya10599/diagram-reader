"""app/income_table.py — SFN 529 (North Dakota LIHEAP) page 3's INCOME
table: Household Members | Employer | How Often Paid | Last Month | This
Month | Next Month.

The person can upload any number of charts, in any order. Two genuinely
agentic questions have to be answered before any of them can be used:

  1. Is this chart even relevant to this form's income table? (A pie chart
     of grocery spending, or literally anything unrelated, should be
     rejected with a clear reason — not silently ignored or, worse,
     force-fit into a row.)
  2. If it IS relevant, which of the three required periods — last month,
     this month, next month — does it represent? The form only has three
     columns; a chart titled "August income" that doesn't map cleanly to
     one of those three is just as unusable as an irrelevant chart.

Neither question is decidable by code alone — both require actually
looking at the image (title, axis labels, what the bars represent) and
making a judgment call, which is why _classify_income_chart is a real
Claude call, not a keyword check. It deliberately runs BEFORE the full
extraction+verification pipeline, so an irrelevant upload costs one cheap
classification call instead of a full multi-round extraction.

Once a chart is classified and accepted, the actual number-reading reuses
graph.py's extraction+verification pipeline completely unchanged — that
part was already built and tested for exactly this shape of chart
(single-value-per-label bar chart), so there's no reason to duplicate or
replace it here. The remaining step, matching household-member labels
across the accepted charts into rows, stays deterministic (normalized
string equality) rather than agentic: each individual chart's numbers are
already verified by that point, so what's left to get wrong is consistent
labeling across images, which is a data-entry concern a second Claude call
wouldn't reliably catch either.

Employer and How Often Paid are never attempted from any chart — they're
not chart data, just text sitting near one — so they're always left blank
for manual entry, matching the same 'flag rather than guess' pattern used
everywhere else in this app.
"""

import logging
import re

from anthropic import Anthropic
from fastapi import HTTPException

from .config import CLAUDE_MODEL
from .graph import analyze_chart
from .llm_agent import _extract_json, _parse_image_data_url, get_client
from .models import (
    AnalyzeChartRequest,
    IncomeRow,
    IncomeTableRequest,
    IncomeTableResponse,
    RejectedImage,
    SeriesPoint,
)

logger = logging.getLogger("diagram_reader")

VALID_PERIODS = ("last_month", "this_month", "next_month")

CLASSIFY_INCOME_CHART_SYSTEM_PROMPT = """You are the first step of a pipeline that fills in the INCOME \
table of a LIHEAP (Low Income Home Energy Assistance Program) application. That table needs bar charts \
showing a household's gross income by household member, one chart each for last month, this month, and \
next month.

You are shown ONE chart image, uploaded by the applicant. Decide two things:

1. Is this chart actually relevant — a bar chart (or similar) where each bar/category represents a PERSON \
and the value is a DOLLAR amount of income? Charts about anything else (expenses/spending, unrelated \
topics, non-income data, or data that isn't broken down by person) are NOT relevant, even if they happen \
to be a bar chart of dollar amounts.

2. If it IS relevant, which of exactly three periods does it represent — "last_month", "this_month", or \
"next_month"? Use the chart's own title, axis labels, or caption text to decide (e.g. a title containing \
"Last Month" maps to last_month). If the chart doesn't make its period clear enough to confidently assign \
one of these three exact values, treat it as NOT usable and explain why — do not guess.

Respond with ONLY a single JSON object (no markdown fences, no commentary outside the JSON):

{
  "relevant": true | false,
  "period": "last_month" | "this_month" | "next_month" | null,
  "reason": "<one sentence: why relevant/not, or why the period couldn't be confidently determined>"
}

"period" must be null whenever "relevant" is false, and must also be null if relevant is true but the \
period genuinely can't be determined with confidence."""


def _classify_income_chart(client: Anthropic, media_type: str, base64_data: str) -> dict:
    """The one agentic step: relevance + period judgment. Cheap and fast on
    purpose (effort=low, no tools, no verification loop) — this exists to
    filter out unusable uploads BEFORE spending a full extraction pass on
    them, not to read the chart's actual numbers."""
    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=500,
            thinking={"type": "adaptive"},
            system=CLASSIFY_INCOME_CHART_SYSTEM_PROMPT,
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
                        {"type": "text", "text": "Classify this chart per your instructions."},
                    ],
                }
            ],
            output_config={"effort": "low"},
        )
    except Exception as exc:
        logger.exception("Income chart classification call failed")
        raise HTTPException(status_code=502, detail=f"Chart classification failed: {exc}")

    if response.stop_reason == "max_tokens":
        raise HTTPException(
            status_code=502,
            detail="Chart classification response was cut off (hit max_tokens).",
        )

    raw_text = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    )
    return _extract_json(raw_text)


def _normalize_label(label: str) -> str:
    """Case/whitespace/punctuation-insensitive key for matching a household
    member's name across separately-extracted charts. Real limitation, not
    just an implementation detail: it only handles formatting differences
    ("Maria Lopez" vs "maria  lopez" vs "Maria-Lopez"), not genuinely
    different renderings of the same name ("M. Lopez" vs "Maria Lopez") —
    the uploaded charts need to use the same name spelling for a person to
    land in one row instead of two."""
    return re.sub(r"[^a-z0-9]", "", label.strip().lower())


def analyze_income_table(payload: IncomeTableRequest) -> IncomeTableResponse:
    if not payload.images:
        raise HTTPException(status_code=400, detail="Upload at least one chart.")

    client = get_client()
    period_series: dict[str, list[SeriesPoint]] = {}
    rejected: list[RejectedImage] = []

    for index, image in enumerate(payload.images):
        try:
            media_type, base64_data = _parse_image_data_url(image)
        except HTTPException as exc:
            rejected.append(RejectedImage(index=index, reason=str(exc.detail)))
            continue

        classification = _classify_income_chart(client, media_type, base64_data)
        relevant = bool(classification.get("relevant"))
        period = classification.get("period")
        reason = classification.get("reason") or ""

        if not relevant or period not in VALID_PERIODS:
            rejected.append(
                RejectedImage(
                    index=index,
                    reason=reason
                    or "This chart doesn't look like a usable household-income-by-person chart "
                    "for one of this form's three required periods (last/this/next month).",
                )
            )
            logger.info("Income table: rejected image %d (%s)", index, reason)
            continue

        if period in period_series:
            rejected.append(
                RejectedImage(
                    index=index,
                    reason=f"Another chart already provided {period.replace('_', ' ')} income — "
                    "only the first one uploaded for a given period is used.",
                )
            )
            continue

        logger.info("Income table: accepted image %d as %s, running extraction", index, period)
        result = analyze_chart(AnalyzeChartRequest(image=image, task=None))
        period_series[period] = result.structuredData.series

    # normalized label -> {"display_label": str, "last_month": float, ...}
    rows_by_key: dict[str, dict] = {}
    for period, series in period_series.items():
        for point in series:
            key = _normalize_label(point.label)
            if not key:
                continue
            row = rows_by_key.setdefault(key, {"display_label": point.label})
            row[period] = point.value

    rows = [
        IncomeRow(
            household_member=data["display_label"],
            last_month=data.get("last_month"),
            this_month=data.get("this_month"),
            next_month=data.get("next_month"),
        )
        for data in rows_by_key.values()
    ]

    incomplete = [
        r.household_member
        for r in rows
        if r.last_month is None or r.this_month is None or r.next_month is None
    ]

    if rows:
        summary = f"Found {len(rows)} household member(s) across {len(period_series)} accepted chart(s)."
    else:
        summary = "No usable charts were accepted — see the rejected images below."
    if incomplete:
        summary += (
            f" Missing at least one month for: {', '.join(incomplete)} — "
            "upload the missing period's chart, or check that all charts use the same spelling of their name."
        )
    if rejected:
        summary += f" {len(rejected)} uploaded image(s) were rejected — see details below."

    return IncomeTableResponse(rows=rows, rejectedImages=rejected, summary=summary)
