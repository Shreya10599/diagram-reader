"""app/vera.py — the one call packages/vera-frontend actually uses.

VERA's mockApi.js defines the contract: analyzeSource(source) -> { fields:
{name, address, min, max, average}, summary }. Rather than being a third,
separate pipeline, this reuses the two graphs that already exist:

  1. graph.py's extraction + verification LangGraph reads the chart (same
     axis-calibration extraction, same verify/correct loop LIHEAP and
     stock_basis rely on — nothing chart-reading-specific is duplicated
     here).
  2. Once that series is verified, min/max/average are computed
     deterministically in plain Python (see _compute_stats) — there's
     nothing for a model to decide here, the series is already trustworthy
     by this point, so a second Claude call to "compute the average" would
     just be slower and less reliable than arithmetic.
  3. Those three computed values are run through form_graph.py's
     validate_mapping — the SAME agentic accuracy gate LIHEAP/stock_basis
     use — before being handed back as fields. This is deliberately NOT
     skipped: it's still a real, if simple, sanity check (confidence,
     plausibility, optional recheck_source_value) before a number goes in
     front of someone who may not double-check it carefully themselves.

name/address are never computable from a chart, so they're always left
blank ("") for the person to fill in themselves — form_schemas.py's
vera_summary schema marks them "manual" and map_to_schema_node routes them
straight to manual_required with no agent involvement.
"""

from statistics import mean

from fastapi import HTTPException

from .form_graph import fill_form
from .graph import analyze_chart
from .models import (
    AnalyzeChartRequest,
    ComputedAnswer,
    FormFillRequest,
    SeriesPoint,
    VeraAnalyzeRequest,
    VeraAnalyzeResponse,
    VeraFields,
)


def _format_number(value: float) -> str:
    """VERA's form fields are plain text <input>s, not numbers — format
    without trailing .0 noise for whole numbers, otherwise round to 2dp."""
    rounded = round(value, 2)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:.2f}".rstrip("0").rstrip(".")


def _compute_stats(series: list[SeriesPoint]) -> list[ComputedAnswer]:
    """Deterministic min/max/average from the ALREADY-VERIFIED series —
    analyze_chart's extraction+verification loop has already run by the
    time this is called, so this is plain arithmetic, not a second Claude
    call. Each answer is tagged with field_id so form_graph.py's
    map_to_schema_node can match it to VERA's min/max/average fields."""
    if not series:
        raise HTTPException(
            status_code=422,
            detail="Couldn't read any data points from this chart to compute min/max/average.",
        )

    values = [p.value for p in series]
    min_point = min(series, key=lambda p: p.value)
    max_point = max(series, key=lambda p: p.value)
    avg_value = mean(values)
    labels = [p.label for p in series]

    return [
        ComputedAnswer(
            label="Minimum value",
            value=min_point.value,
            formula=f"min({', '.join(str(v) for v in values)}) = {min_point.value}",
            sourcePoints=[min_point.label],
            field_id="min",
        ),
        ComputedAnswer(
            label="Maximum value",
            value=max_point.value,
            formula=f"max({', '.join(str(v) for v in values)}) = {max_point.value}",
            sourcePoints=[max_point.label],
            field_id="max",
        ),
        ComputedAnswer(
            label="Average",
            value=avg_value,
            formula=f"({' + '.join(str(v) for v in values)}) / {len(values)} = {avg_value:.4f}",
            sourcePoints=labels,
            field_id="average",
        ),
    ]


def analyze_for_vera(payload: VeraAnalyzeRequest) -> VeraAnalyzeResponse:
    """VERA's one call: read the chart, compute min/max/average, validate
    those three values agentically, and return the fields + summary shape
    vera-frontend's mockApi.js contract expects."""
    chart_result = analyze_chart(AnalyzeChartRequest(image=payload.image, task=None))

    computed_answers = _compute_stats(chart_result.structuredData.series)

    worksheet = fill_form(
        FormFillRequest(
            formType="vera_summary",
            computedAnswers=computed_answers,
            image=payload.image,
            extractedSeries=chart_result.structuredData,
        )
    )

    fields = VeraFields()
    caveats = []
    for result in worksheet.fields:
        if result.field_id in ("min", "max", "average") and result.value is not None:
            setattr(fields, result.field_id, _format_number(result.value))
        if result.status == "needs_review":
            caveats.append(f"double-check the {result.label.lower()} — {result.reason}")

    summary = chart_result.shortDescription or chart_result.description
    if caveats:
        summary += " One thing to double check: " + "; ".join(caveats) + "."

    return VeraAnalyzeResponse(fields=fields, summary=summary)
