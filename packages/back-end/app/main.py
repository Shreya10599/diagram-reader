import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .form_graph import fill_form
from .form_schema_extraction import extract_form_schema
from .graph import analyze_chart
from .income_pdf import fill_liheap_form_from_chart, fill_liheap_income_table
from .income_table import analyze_income_table
from .models import (
    AnalyzeChartRequest,
    AnalyzeChartResponse,
    FillFormFromChartResponse,
    FillIncomeTablePdfRequest,
    FillIncomeTablePdfResponse,
    FormFillRequest,
    FormFillResponse,
    FormSchemaExtractionRequest,
    FormSchemaExtractionResponse,
    IncomeTableRequest,
    IncomeTableResponse,
    VeraAnalyzeRequest,
    VeraAnalyzeResponse,
)
from .vera import analyze_for_vera

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Diagram Reader API")

# CORS origins: the two Vite dev ports (packages/front-end on 5173,
# packages/vera-frontend on 5174 — see vera-frontend/vite.config.js) are
# always allowed so local dev keeps working unchanged. In production, set
# ALLOWED_ORIGINS to a comma-separated list of the deployed frontend
# origin(s) — e.g. "https://vera-frontend.vercel.app" — as an env var on
# whatever host runs this (Render, etc.). No code change/redeploy needed to
# add or change an allowed origin later.
_dev_origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5174",
]
_prod_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_dev_origins + _prod_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def healthcheck():
    return {"status": "ok"}


@app.post("/analyze-chart", response_model=AnalyzeChartResponse)
async def analyze_chart_endpoint(payload: AnalyzeChartRequest) -> AnalyzeChartResponse:
    """Accepts a chart image (base64 data URL) plus an optional ChartTask,
    runs the Claude extraction agent, and returns the structured result."""
    return analyze_chart(payload)


@app.post("/fill-form", response_model=FormFillResponse)
async def fill_form_endpoint(payload: FormFillRequest) -> FormFillResponse:
    """Separate call, deliberately not chained onto /analyze-chart — takes
    the computedAnswer/extractedSeries a prior /analyze-chart call already
    produced plus a target formType, maps it onto that form's hardcoded
    schema, and runs validate_mapping (the one agentic step) on each
    computedAnswer-sourced field before returning a worksheet the user
    reviews and copies into the real form themselves."""
    return fill_form(payload)


@app.post("/vera/analyze", response_model=VeraAnalyzeResponse)
async def vera_analyze_endpoint(payload: VeraAnalyzeRequest) -> VeraAnalyzeResponse:
    """The one call packages/vera-frontend's mockApi.js needs to stop being
    a mock: reads the chart, computes min/max/average, runs those through
    validate_mapping (form_graph.py's agentic accuracy gate, reused as-is),
    and returns the { fields, summary } shape VERA's UI expects. See
    app/vera.py for why each step is or isn't agentic."""
    return analyze_for_vera(payload)


@app.post("/liheap/income-table", response_model=IncomeTableResponse)
async def income_table_endpoint(payload: IncomeTableRequest) -> IncomeTableResponse:
    """SFN 529 (North Dakota LIHEAP) page 3's INCOME table. Takes any
    number of chart images, in any order; each is classified for
    relevance + period before the extraction+verification pipeline runs
    on it, and rejected charts are reported with a reason rather than
    silently dropped or force-fit — see app/income_table.py."""
    return analyze_income_table(payload)


@app.post("/extract-form-schema", response_model=FormSchemaExtractionResponse)
async def extract_form_schema_endpoint(
    payload: FormSchemaExtractionRequest,
) -> FormSchemaExtractionResponse:
    """Reads a user-uploaded form (PDF or a photo of one) and extracts its
    fillable fields, each classified computedAnswer/manual. The result's
    `fields` feeds straight into POST /fill-form as
    formType="custom", customSchema=fields — see
    app/form_schema_extraction.py for why this needed a second agentic
    step alongside validate_mapping, and form_graph.py's
    select_form_schema_node for how it plugs into the existing pipeline
    unchanged."""
    return extract_form_schema(payload)


@app.post("/liheap/fill-income-table-pdf", response_model=FillIncomeTablePdfResponse)
async def fill_income_table_pdf_endpoint(payload: FillIncomeTablePdfRequest) -> FillIncomeTablePdfResponse:
    """Takes ONE grouped household-income chart (member x last/this/next
    month, all in one image — the shape people actually upload) plus the
    real LIHEAP form's own PDF, and returns an actual filled copy of that
    PDF's page 3 income table as base64 — not another JSON worksheet. See
    app/income_pdf.py for the two Claude calls this needs (reading the
    chart, locating the table on a form we've never seen the layout of)
    and the plain pypdf/reportlab mechanics that do the actual drawing."""
    return fill_liheap_income_table(payload)


@app.post("/liheap/fill-form-from-chart", response_model=FillFormFromChartResponse)
async def fill_form_from_chart_endpoint(payload: FillIncomeTablePdfRequest) -> FillFormFromChartResponse:
    """The general version of the endpoint above: the person can upload
    EITHER an income chart or an expenses chart (same two-field request
    shape — chartImage, formFile), and this decides which one it is before
    picking page 3 (income) or page 5 (expenses) to fill. Chart-reading
    itself goes through the exact same LangGraph extraction+verification
    pipeline as every other chart in this app (graph.py, unchanged) — see
    app/income_pdf.py's module docstring for the three agentic judgments
    this flow makes and why none of them could be decided by code alone."""
    return fill_liheap_form_from_chart(payload)
