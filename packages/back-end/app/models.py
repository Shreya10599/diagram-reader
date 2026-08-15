from typing import Literal, Optional

from pydantic import BaseModel


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


class SeriesCorrection(BaseModel):
    label: str
    value: float


class VerificationResult(BaseModel):
    """Verdict from the verification pass: does the re-rendered chart match
    the original, and if not, what specific values should be corrected? This
    is the branch point that lets Claude — not the calling code — decide
    whether the extraction loop continues and what it fixes next."""

    match: bool
    corrections: list[SeriesCorrection] = []
    notes: str = ""
