from typing import Literal, Optional

from pydantic import BaseModel

from .form_schemas import FormField


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
    # Which target form field this answer is meant to fill, e.g. "min" /
    # "max" / "average" for VERA's generic form. Optional because the plain
    # /analyze-chart response doesn't need it (there's only ever one
    # computedAnswer there) — only form-filling requests with more than one
    # computed value per request (see FormFillRequest.computedAnswers) need
    # this to know which field each answer belongs to.
    field_id: Optional[str] = None


class AnalyzeChartResponse(BaseModel):
    description: str
    shortDescription: str
    structuredData: StructuredData
    computedAnswer: Optional[ComputedAnswer] = None


class SeriesCorrection(BaseModel):
    label: str
    # None means "this point doesn't actually appear in the original chart
    # — remove it" rather than "correct its value" (see
    # VERIFICATION_SYSTEM_PROMPT and llm_agent._apply_corrections). Needed
    # after real testing showed the verification pass legitimately
    # flagging a hallucinated extra point past the last real one, with no
    # valid way to express "delete" under the old value:float-required
    # schema.
    value: Optional[float] = None


class VerificationResult(BaseModel):
    """Verdict from the verification pass: does the re-rendered chart match
    the original, and if not, what specific values should be corrected? This
    is the branch point that lets Claude — not the calling code — decide
    whether the extraction loop continues and what it fixes next."""

    match: bool
    corrections: list[SeriesCorrection] = []
    notes: str = ""


# ---- Form-filling models ----------------------------------------------------
# The extraction/verification graph above ends with a trustworthy
# StructuredData + ComputedAnswer. These models cover the second, separate
# graph (form_graph.py) that maps a ComputedAnswer onto a known target
# form's fields and validates that mapping — see form_schemas.py for why
# the field *mapping* is hardcoded rather than agent-inferred, and
# form_agent.py for why *validating* it is the agentic part instead.


class FormFillRequest(BaseModel):
    # "vera_summary" added alongside the original two: VERA's landing form
    # needs three computedAnswer-sourced fields at once (min/max/average),
    # not just one the way LIHEAP/stock_basis did — see form_schemas.py.
    # "custom" is for a schema extracted at runtime from a user-uploaded
    # form (see form_schema_extraction.py) instead of a hardcoded one.
    formType: Literal["liheap", "stock_basis", "vera_summary", "custom"]
    # Only used (and required) when formType == "custom" — the schema
    # extracted from the uploaded form. Ignored for the three hardcoded
    # formTypes, which still come from form_schemas.get_schema() instead.
    customSchema: Optional[list[FormField]] = None
    # One entry per computedAnswer-sourced field in the target schema,
    # matched by ComputedAnswer.field_id -> FormField.field_id (see
    # form_graph.py's map_to_schema_node). LIHEAP/stock_basis only ever
    # need one entry; vera_summary needs three (min, max, average).
    computedAnswers: list[ComputedAnswer]
    # The original chart image + its extracted series are needed so
    # validate_mapping's recheck_source_value tool can re-read specific
    # points from the real chart rather than trusting computedAnswer blindly.
    image: str
    extractedSeries: StructuredData


class FieldValidationVerdict(BaseModel):
    """Verdict from one validate_mapping agent call on a single
    computedAnswer-sourced field. Mirrors VerificationResult's role: this is
    the branch point where Claude — not the calling code — decides whether
    a mapped value is trustworthy enough to hand to the user, needs a second
    look (recheck_source_value), or should be flagged for a human instead of
    silently shipped."""

    status: Literal["accepted", "flagged"]
    value: Optional[float] = None  # possibly corrected after a recheck
    reason: str = ""


class WorksheetFieldResult(BaseModel):
    """One row of the final worksheet handed back to the user — the actual
    'helper worksheet, not auto-submitter' output. `status` tells the
    front-end how to render it: a filled value, a value that needs the
    user's own double-check, or a field the chart pipeline could never have
    answered in the first place."""

    field_id: str
    label: str
    value: Optional[float] = None
    unit: Optional[str] = ""
    status: Literal["filled", "needs_review", "manual_required"]
    reason: str = ""
    sourcePoints: list[str] = []


class FormFillResponse(BaseModel):
    formType: str
    fields: list[WorksheetFieldResult]
    summary: str


# ---- VERA models --------------------------------------------------------------
# packages/vera-frontend's mockApi.js defines the contract this has to match
# exactly: analyzeSource(source) -> { fields: {name,address,min,max,average},
# summary }. `fields` values are strings (they go straight into <input>
# elements), not numbers — see vera.py for the formatting.


class VeraFields(BaseModel):
    name: str = ""
    address: str = ""
    min: str = ""
    max: str = ""
    average: str = ""


class VeraAnalyzeRequest(BaseModel):
    # Only a photo/upload data URL for now — vera-frontend's "Add a link"
    # option stays on the front-end mock until server-side URL fetching
    # (with SSRF-safe validation) is built.
    image: str


class VeraAnalyzeResponse(BaseModel):
    fields: VeraFields
    summary: str


# ---- LIHEAP income-table models ------------------------------------------------
# Targets SFN 529 (North Dakota LIHEAP) page 3's INCOME table: Household
# Members | Employer | How Often Paid | Last Month | This Month | Next
# Month. Unlike every other form schema here, this isn't a fixed list of
# named fields — it's a variable number of ROWS, one per household member,
# and the members themselves are only known after reading the charts. So
# instead of extending form_schemas.py's fixed-field model, this gets its
# own request/response shape and its own module (income_table.py).
#
# The user can upload any number of charts, in any order — not a fixed
# "chart #1 = last month" slot system. Each one is independently classified
# (the one agentic step here: is this chart even relevant to this form's
# income table, and if so, which of the three periods does it represent?)
# before the expensive extraction+verification pipeline runs on it.
# Irrelevant, ambiguous, or duplicate-period charts are rejected with a
# per-image reason rather than silently processed.


class IncomeRow(BaseModel):
    """One row of the INCOME table. household_member/last_month/this_month/
    next_month come from matching labels across whichever charts were
    accepted. employer and how_often_paid are never chart data — they're
    always left blank for the person to fill in themselves, same as any
    other manual_required field elsewhere in this app."""

    household_member: str
    employer: str = ""
    how_often_paid: str = ""
    last_month: Optional[float] = None
    this_month: Optional[float] = None
    next_month: Optional[float] = None


class RejectedImage(BaseModel):
    """One uploaded chart that couldn't be used, and why — surfaced to the
    user as an explicit per-image error rather than silently dropped or
    (worse) processed as if it were valid data."""

    index: int
    reason: str


class IncomeTableRequest(BaseModel):
    images: list[str]  # any number of data URLs, in any order


class IncomeTableResponse(BaseModel):
    rows: list[IncomeRow]
    rejectedImages: list[RejectedImage] = []
    summary: str


# ---- Income-table-to-real-PDF models -------------------------------------------
# The "actually download a filled copy of the real form" path — see
# app/income_pdf.py. Distinct from IncomeTableRequest/Response above: this
# takes ONE grouped chart (household member x last/this/next month, all in
# one image, per the shape people actually upload) plus the form's own PDF,
# and hands back a real filled PDF instead of another JSON worksheet.


class FillIncomeTablePdfRequest(BaseModel):
    chartImage: str  # data URL of the grouped household-income chart
    formFile: str  # data URL of the LIHEAP form — PDF or a photo of one


class FillIncomeTablePdfResponse(BaseModel):
    pdfBase64: str  # the filled form, as a downloadable PDF
    rows: list[IncomeRow]
    summary: str


# ---- Classify-then-fill models --------------------------------------------------
# The general version of the above: the person can upload EITHER an income
# chart or an expenses chart, and the backend decides which (see
# app/income_pdf.py's _classify_chart_topic) before deciding whether page 3
# (income) or page 5 (expenses) gets filled. Reuses FillIncomeTablePdfRequest
# as its request shape (same two fields: chartImage, formFile) rather than
# duplicating an identical model.


class ExpenseRow(BaseModel):
    """One row of the EXPENSES table actually written onto the form. Mirrors
    IncomeRow's shape: who_for/date_paid/frequency are never attempted from
    a chart (not chart data), so they're left blank for manual entry, same
    'flag rather than guess' pattern as employer/how_often_paid on the
    income side."""

    expense_type: str
    amount: Optional[float] = None
    who_for: str = ""
    date_paid: str = ""
    frequency: str = ""


class ExcludedExpenseCategory(BaseModel):
    """A category the uploaded chart had, but that the form-reading step
    judged isn't a legitimate LIHEAP-recognized expense type (e.g.
    "Entertainment" or "Savings" from a generic budget-tracker chart) —
    surfaced with a reason instead of being silently written onto a real
    government form or silently dropped."""

    category: str
    reason: str = ""


class FillFormFromChartResponse(BaseModel):
    section: Literal["income", "expenses"]
    pdfBase64: str
    incomeRows: list[IncomeRow] = []
    expenseRows: list[ExpenseRow] = []
    excludedExpenseCategories: list[ExcludedExpenseCategory] = []
    summary: str


# ---- Dynamic form-schema extraction models --------------------------------------
# Lets the person upload the actual target form (PDF or a photo of it)
# instead of the app assuming everyone needs the same hardcoded form —
# see form_schema_extraction.py. The extracted fields feed straight into
# FormFillRequest(formType="custom", customSchema=fields) above, so the
# rest of the form-filling pipeline (map_to_schema, validate_mapping,
# etc.) runs completely unchanged on a schema it's never seen before.


class ExtractedFormField(BaseModel):
    """One field the schema-extraction agent found on the uploaded form.
    Same shape as form_schemas.FormField (field_id/label/unit/source) plus
    `reason`, kept here rather than merged into FormField because `reason`
    only makes sense for a dynamically classified field, not a hardcoded
    one — pydantic drops the extra key harmlessly when this list is later
    passed straight into FormFillRequest.customSchema: list[FormField]."""

    field_id: str
    label: str
    unit: str = ""
    source: Literal["computedAnswer", "manual"]
    reason: str = ""


class FormSchemaExtractionRequest(BaseModel):
    # A data URL — either "data:application/pdf;base64,..." (sent to
    # Claude as a document content block) or "data:image/...;base64,..."
    # (a photo of the form, sent as an image block).
    formFile: str


class FormSchemaExtractionResponse(BaseModel):
    formTitle: str
    fields: list[ExtractedFormField]
    # Repeating-row table sections (e.g. a household-members table) can't
    # be represented as a flat field list — their titles are listed here
    # instead of being silently dropped. See income_table.py for the
    # separate flow that handles exactly this kind of table.
    skippedSections: list[str] = []
