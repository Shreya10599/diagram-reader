"""app/income_pdf.py — "read a chart, figure out whether it's income or
expenses, fill the right table on a real LIHEAP PDF, hand back a
downloadable filled copy" flow.

Chart-reading itself deliberately does NOT reinvent extraction: every
number in this file comes from graph.py's existing LangGraph extraction +
verification pipeline (extract -> render -> verify_pass -> correct ->
finalize), completely unchanged. That pipeline is what re-renders the
extracted table and has Claude compare it against the original before
trusting it, and it's the same pipeline every other chart-reading path in
this app goes through — there's no reason (and it would be a real
accuracy regression) to bypass it here with a one-shot call. See
_read_income_chart / _read_expense_chart below for how each chart shape
threads a task instruction through it, and llm_agent._build_user_instruction
for the one small extension that made a grouped/clustered bar chart
possible to describe as a task at all.

Three genuinely agentic judgments live in this file, each answering a
question code can't decide in advance:

1. _classify_chart_topic — is the uploaded chart even usable, and if so,
   does it belong on the INCOME table (page 3, income by household member)
   or the EXPENSES table (page 5, expenses by category)? Runs cheap and
   first, before spending a full extraction+verification pass on an
   upload that might not be usable at all — same reasoning as
   income_table.py's _classify_income_chart.

2. The task instructions handed into the LangGraph pipeline (see above) —
   telling Claude HOW to read a shape of chart the base extraction
   procedure has no built-in concept of (grouped bars with a legend).

3. _locate_income_table / _locate_expense_table — reads the uploaded form
   fresh (not a template we already know) to find the right page and
   approximate cell positions; _locate_expense_table additionally has to
   decide which of the chart's expense categories are the kind of expense
   this form's table is actually asking about (rent, medical, child care,
   ...) versus generic personal-budget categories that don't belong on a
   LIHEAP application (entertainment, savings, ...) — a real judgment call
   that would otherwise mean silently writing wrong information onto a
   real government form.

Everything else — converting fractions to PDF points, drawing text with
reportlab, merging pages with pypdf, matching a normalized household-member
name across a re-verified series — is plain code, not agentic, same split
used throughout this app.
"""

import base64
import io
import json
import logging

from anthropic import Anthropic
from fastapi import HTTPException
from pypdf import PdfReader, PdfWriter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from .config import CLAUDE_MODEL
from .form_schema_extraction import _parse_form_data_url
from .graph import analyze_chart
from .llm_agent import _extract_json, _parse_image_data_url, get_client
from .models import (
    AnalyzeChartRequest,
    ChartTask,
    ExcludedExpenseCategory,
    ExpenseRow,
    FillFormFromChartResponse,
    FillIncomeTablePdfRequest,
    FillIncomeTablePdfResponse,
    IncomeRow,
)

logger = logging.getLogger("diagram_reader")


# ---- Step 0: what kind of chart is this? -------------------------------------------

CLASSIFY_CHART_TOPIC_SYSTEM_PROMPT = """You are the first step of a pipeline that fills in a LIHEAP \
(Low Income Home Energy Assistance Program) application from a chart the applicant uploads. This \
application has two different chart-fillable tables:

- An INCOME table (page 3): a household's GROSS INCOME broken down by household member (a grouped/ \
clustered bar chart, one group per person, with last/this/next month as colored sub-bars per a legend).
- An EXPENSES table (page 5): the household's recurring expenses broken down by category/type (e.g. a \
bar or pie chart with categories like Rent, Medical, Child Care, and a dollar amount for each).

Look at the uploaded chart and decide which of these two it's useful for, if either:

- "income" — bars/values broken down by PERSON (household member), not by expense category.
- "expenses" — bars/values broken down by EXPENSE CATEGORY/TYPE, not by person.
- "other" — neither of these (unrelated topic, or too ambiguous to confidently classify).

Respond with ONLY a single JSON object (no markdown fences, no commentary outside the JSON):

{
  "topic": "income" | "expenses" | "other",
  "reason": "<one sentence explaining the classification>"
}"""


def _classify_chart_topic(client: Anthropic, media_type: str, base64_data: str) -> dict:
    """Cheap triage call (effort=low, no tools, no verification loop) run
    BEFORE the expensive extraction+verification pipeline — filters out
    unusable uploads and picks income vs expenses without paying for a
    full pass on a chart that might not even be relevant."""
    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=500,
            thinking={"type": "adaptive"},
            system=CLASSIFY_CHART_TOPIC_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": media_type, "data": base64_data},
                        },
                        {"type": "text", "text": "Classify this chart per your instructions."},
                    ],
                }
            ],
            output_config={"effort": "low"},
        )
    except Exception as exc:
        logger.exception("Chart topic classification failed")
        raise HTTPException(status_code=502, detail=f"Reading that chart failed: {exc}")

    if response.stop_reason == "max_tokens":
        raise HTTPException(
            status_code=502,
            detail="Chart classification response was cut off (hit max_tokens).",
        )

    raw_text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
    return _extract_json(raw_text)


# ---- Step 1a: read an income chart, via the SAME LangGraph pipeline ----------------
# as every other chart in this app -----------------------------------------------------

GROUPED_INCOME_TASK_INSTRUCTION = (
    "This chart groups bars by household member along the x-axis, with three colored sub-bars per "
    "member for last month / this month / next month income (see the legend for which color is which "
    "period). When you build structuredData.series, use ONE point per bar (one per member-period "
    'combination), with each point\'s "label" formatted EXACTLY as "<member name> — <period>" using '
    'one of these three exact period strings: "Last month", "This month", "Next month" (for example, '
    '"Maria Lopez — Last month"). Read each member\'s name exactly as labeled on the chart. No further '
    "computation is needed — omit computedAnswer."
)

_PERIOD_KEY_BY_LABEL = {
    "last month": "last_month",
    "this month": "this_month",
    "next month": "next_month",
}


def _parse_income_series_to_rows(series: list) -> list[IncomeRow]:
    """Deterministic, NOT agentic — parses the compound "<member> — <period>"
    labels the LangGraph pipeline was instructed to produce, and groups them
    into one IncomeRow per member. This is the same kind of step as
    income_table.py's normalized-label row matching: the pipeline has
    already verified every individual number by this point (re-rendered the
    chart, compared it against the original, corrected any mismatches), so
    what's left — splitting a label on its delimiter and grouping by member
    — is plain string handling a second Claude call wouldn't do any more
    reliably."""
    rows_by_key: dict[str, dict] = {}
    order: list[str] = []

    for point in series:
        label = point.label
        member_part, sep, period_part = label.rpartition("—")
        if not sep:
            member_part, sep, period_part = label.rpartition("-")
        if not sep:
            logger.warning("Income series point has no parseable '<member> — <period>' label: %r", label)
            continue

        member = member_part.strip()
        period_key = _PERIOD_KEY_BY_LABEL.get(period_part.strip().lower())
        if not member or not period_key:
            logger.warning("Income series point label didn't split into a known member/period: %r", label)
            continue

        key = member.lower()
        if key not in rows_by_key:
            rows_by_key[key] = {"display_label": member}
            order.append(key)
        rows_by_key[key][period_key] = point.value

    return [
        IncomeRow(
            household_member=rows_by_key[k]["display_label"],
            last_month=rows_by_key[k].get("last_month"),
            this_month=rows_by_key[k].get("this_month"),
            next_month=rows_by_key[k].get("next_month"),
        )
        for k in order
    ]


def _read_income_chart(chart_image_data_url: str) -> list[IncomeRow]:
    result = analyze_chart(
        AnalyzeChartRequest(
            image=chart_image_data_url,
            task=ChartTask(type="full_extraction", instruction=GROUPED_INCOME_TASK_INSTRUCTION),
        )
    )
    rows = _parse_income_series_to_rows(result.structuredData.series)
    if not rows:
        raise HTTPException(
            status_code=422,
            detail="Couldn't find any household members' income in that chart after verification — "
            "is it a grouped bar chart with a last/this/next month legend?",
        )
    return rows


# ---- Step 1b: read an expenses chart, same pipeline, no parsing needed -------------
# (a category-by-amount chart is already exactly the flat {label, value} shape the
# base extraction procedure produces on its own).

EXPENSE_CHART_TASK_INSTRUCTION = (
    "This chart shows a household's expenses broken down by category (e.g. Rent, Medical, "
    "Transportation, Child Care), with a dollar amount for each. Read each category's exact label and "
    "value. No further computation is needed — omit computedAnswer."
)


def _read_expense_chart(chart_image_data_url: str) -> list[dict]:
    result = analyze_chart(
        AnalyzeChartRequest(
            image=chart_image_data_url,
            task=ChartTask(type="full_extraction", instruction=EXPENSE_CHART_TASK_INSTRUCTION),
        )
    )
    expenses = [
        {"category": point.label, "amount": point.value} for point in result.structuredData.series
    ]
    if not expenses:
        raise HTTPException(
            status_code=422,
            detail="Couldn't find any expense categories in that chart after verification.",
        )
    return expenses


# ---- Step 2a: locate the income table on the uploaded form -------------------------

LOCATE_INCOME_TABLE_SYSTEM_PROMPT = """You are looking at a real government benefits application form \
(PDF or a photo of one), searching for its INCOME table — a table with a row per household member and \
columns for Last Month Income, This Month Income, and Next Month Income (column names may vary slightly, \
e.g. "Income Last Month").

Report back where the FIRST BLANK DATA ROW of that table is, and where each amount column's text should \
start, so a program can draw filled-in values at the right spot on the ORIGINAL page image/PDF page:

- page_index: 0-based index of the page this table is on.
- household_member_x_frac: fraction (0.0-1.0) of the page's WIDTH, left edge, where household member \
names should start.
- last_month_x_frac / this_month_x_frac / next_month_x_frac: same, for each amount column's left edge.
- first_row_y_frac: fraction (0.0-1.0) of the page's HEIGHT, measured FROM THE TOP, at the vertical \
center of the FIRST blank data row (not the header row).
- row_height_frac: fraction (0.0-1.0) of the page's HEIGHT representing the vertical spacing between \
one data row and the next.

Respond with ONLY a single JSON object (no markdown fences, no commentary outside the JSON), matching \
exactly this shape:

{
  "page_index": <int>,
  "household_member_x_frac": <number>,
  "last_month_x_frac": <number>,
  "this_month_x_frac": <number>,
  "next_month_x_frac": <number>,
  "first_row_y_frac": <number>,
  "row_height_frac": <number>
}

If you cannot find a table matching this description anywhere in the document, respond with \
{"error": "<one sentence explaining what you looked at and why nothing matched>"} instead."""


def _content_block_for_form(media_type: str, base64_data: str) -> dict:
    if media_type == "application/pdf":
        return {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": base64_data},
        }
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": base64_data}}


def _locate_income_table(client: Anthropic, media_type: str, base64_data: str) -> dict:
    content_block = _content_block_for_form(media_type, base64_data)
    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1500,
            thinking={"type": "adaptive"},
            system=LOCATE_INCOME_TABLE_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        content_block,
                        {"type": "text", "text": "Locate the income table per your instructions."},
                    ],
                }
            ],
            output_config={"effort": "medium"},
        )
    except Exception as exc:
        logger.exception("Income table location call failed")
        raise HTTPException(status_code=502, detail=f"Reading that form failed: {exc}")

    if response.stop_reason == "max_tokens":
        raise HTTPException(
            status_code=502,
            detail="Form-reading response was cut off (hit max_tokens) before finishing.",
        )

    raw_text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
    parsed = _extract_json(raw_text)

    if "error" in parsed:
        raise HTTPException(
            status_code=422,
            detail=f"Couldn't find an income table on that form: {parsed['error']}",
        )

    required = (
        "page_index",
        "household_member_x_frac",
        "last_month_x_frac",
        "this_month_x_frac",
        "next_month_x_frac",
        "first_row_y_frac",
        "row_height_frac",
    )
    missing = [key for key in required if key not in parsed]
    if missing:
        raise HTTPException(
            status_code=502,
            detail=f"Form-reading response was missing expected field(s): {', '.join(missing)}",
        )
    return parsed


# ---- Step 2b: locate the expenses table AND judge which categories belong ---------

LOCATE_EXPENSE_TABLE_SYSTEM_PROMPT = """You are looking at a real government benefits application form \
(PDF or a photo of one), searching for its EXPENSES table — a table with a row per expense and columns \
for something like Type of Expense, Who the Expense is For, Amount Paid, Date Paid, and Frequency \
(column names may vary slightly).

Many forms of this kind list the SPECIFIC expense types they recognize as checkboxes or a labeled list \
right above or near the table (for example: "Child Care", "Child Support", "Spousal Support", "Court \
Ordered Garnishments", "Medical Expenses", "Health and Hospitalization Insurance Premiums" — the exact \
set varies by form). If you can find such a list on THIS form, treat it as the authoritative, exhaustive \
definition of what belongs in the table — do not fall back on general assumptions about what a benefits \
application "usually" accepts (rent/mortgage, for instance, is often handled in a separate housing \
section rather than as a page's expense-table category, even though it's a real necessary expense). Only \
if the form gives no such explicit list should you reason from general necessary-household-expense \
categories (child/dependent care, medical costs not covered by insurance, court-ordered support, and \
similar) as opposed to discretionary spending (entertainment, dining out, savings).

You will also be given a list of expense categories read from a chart the applicant uploaded, as JSON, \
in the next message. For EACH category, decide whether it matches one of the form's recognized expense \
types (per the above). If it's a match, keep it and rephrase its label to exactly match how the form \
describes that expense type. If it isn't a match, exclude it and say why in one short sentence.

Report:
- page_index: 0-based index of the page this table is on.
- expense_type_x_frac / amount_x_frac: fraction (0.0-1.0) of the page's WIDTH, left edge, for where the \
expense-type label and the dollar amount should each start.
- first_row_y_frac: fraction (0.0-1.0) of the page's HEIGHT, from the top, at the vertical center of the \
FIRST blank data row (not the header row).
- row_height_frac: fraction (0.0-1.0) of the page's HEIGHT — vertical spacing between rows.
- included: list of objects, one per category you're keeping: \
{ "category": "<original category from the chart>", "label": "<label to write on the form>", \
"amount": <number> }.
- excluded: list of objects, one per category you're leaving out: \
{ "category": "<original category from the chart>", "reason": "<why it doesn't belong on this table>" }.

Respond with ONLY a single JSON object (no markdown fences, no commentary outside the JSON), matching \
exactly this shape:

{
  "page_index": <int>,
  "expense_type_x_frac": <number>,
  "amount_x_frac": <number>,
  "first_row_y_frac": <number>,
  "row_height_frac": <number>,
  "included": [ { "category": "...", "label": "...", "amount": <number> }, ... ],
  "excluded": [ { "category": "...", "reason": "..." }, ... ]
}

If you cannot find a table matching this description anywhere in the document, respond with \
{"error": "<one sentence explaining what you looked at and why nothing matched>"} instead."""


def _locate_expense_table(
    client: Anthropic, media_type: str, base64_data: str, chart_categories: list[dict]
) -> dict:
    content_block = _content_block_for_form(media_type, base64_data)
    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2000,
            thinking={"type": "adaptive"},
            system=LOCATE_EXPENSE_TABLE_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        content_block,
                        {
                            "type": "text",
                            "text": f"Chart categories: {json.dumps(chart_categories)}\n\n"
                            "Locate the expenses table and classify these categories per your "
                            "instructions.",
                        },
                    ],
                }
            ],
            output_config={"effort": "medium"},
        )
    except Exception as exc:
        logger.exception("Expense table location call failed")
        raise HTTPException(status_code=502, detail=f"Reading that form failed: {exc}")

    if response.stop_reason == "max_tokens":
        raise HTTPException(
            status_code=502,
            detail="Form-reading response was cut off (hit max_tokens) before finishing.",
        )

    raw_text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
    parsed = _extract_json(raw_text)

    if "error" in parsed:
        raise HTTPException(
            status_code=422,
            detail=f"Couldn't find an expenses table on that form: {parsed['error']}",
        )

    required = (
        "page_index",
        "expense_type_x_frac",
        "amount_x_frac",
        "first_row_y_frac",
        "row_height_frac",
    )
    missing = [key for key in required if key not in parsed]
    if missing:
        raise HTTPException(
            status_code=502,
            detail=f"Form-reading response was missing expected field(s): {', '.join(missing)}",
        )
    return parsed


# ---- Step 3: draw generic rows onto the real page (not agentic) --------------------
# `rows` here is a list of plain dicts, already formatted as display strings — the
# keys must match "<key>_x_frac" entries in `layout`. Domain-specific row-building
# (IncomeRow -> dict, ExpenseRow -> dict) happens in the callers below, so this stays
# reusable for both tables instead of hard-coding one row shape.


def _draw_rows(c: canvas.Canvas, width: float, height: float, layout: dict, rows: list[dict]) -> None:
    c.setFont("Helvetica", max(9, int(height / 90)))
    y = height - (layout["first_row_y_frac"] * height)
    row_height = layout["row_height_frac"] * height
    for row in rows:
        for field, value in row.items():
            x_key = f"{field}_x_frac"
            if x_key not in layout or value in (None, ""):
                continue
            c.drawString(layout[x_key] * width, y, str(value)[:40])
        y -= row_height


def _fill_pdf_page(pdf_bytes: bytes, layout: dict, rows: list[dict]) -> bytes:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    page_index = layout["page_index"]
    if page_index < 0 or page_index >= len(reader.pages):
        raise HTTPException(
            status_code=422,
            detail=f"The form-reading step pointed at page {page_index + 1}, but this PDF only has "
            f"{len(reader.pages)} page(s).",
        )

    page = reader.pages[page_index]
    width = float(page.mediabox.width)
    height = float(page.mediabox.height)

    overlay_buf = io.BytesIO()
    c = canvas.Canvas(overlay_buf, pagesize=(width, height))
    _draw_rows(c, width, height, layout, rows)
    c.save()
    overlay_buf.seek(0)

    overlay_reader = PdfReader(overlay_buf)
    writer = PdfWriter()
    for i, pg in enumerate(reader.pages):
        if i == page_index:
            pg.merge_page(overlay_reader.pages[0])
        writer.add_page(pg)

    out_buf = io.BytesIO()
    writer.write(out_buf)
    return out_buf.getvalue()


def _fill_image_page(image_bytes: bytes, layout: dict, rows: list[dict]) -> bytes:
    """Same idea as _fill_pdf_page, but the "form" was a photo rather than a
    PDF — draws the photo as the page background and overlays text on top,
    producing a brand-new single-page PDF the person can still download."""
    img = ImageReader(io.BytesIO(image_bytes))
    img_width, img_height = img.getSize()

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(img_width, img_height))
    c.drawImage(img, 0, 0, width=img_width, height=img_height)
    _draw_rows(c, img_width, img_height, layout, rows)
    c.save()
    buf.seek(0)
    return buf.getvalue()


def _fill_form(form_bytes: bytes, form_media_type: str, layout: dict, rows: list[dict]) -> bytes:
    if form_media_type == "application/pdf":
        return _fill_pdf_page(form_bytes, layout, rows)
    return _fill_image_page(form_bytes, layout, rows)


# ---- Top-level entry points ---------------------------------------------------------


def fill_liheap_income_table(payload: FillIncomeTablePdfRequest) -> FillIncomeTablePdfResponse:
    """The original, narrower endpoint — always assumes the chart is an
    income chart (no classification step). Kept working, unchanged
    interface, for whatever already depends on it; internally shares the
    same LangGraph-backed reading and generic drawing code as the newer
    classify-then-fill flow below."""
    rows = _read_income_chart(payload.chartImage)

    form_media_type, form_b64 = _parse_form_data_url(payload.formFile)
    client = get_client()
    layout = _locate_income_table(client, form_media_type, form_b64)

    form_bytes = base64.b64decode(form_b64)
    draw_rows = [
        {
            "household_member": (r.household_member or "")[:28],
            "last_month": f"${r.last_month:,.2f}" if r.last_month is not None else None,
            "this_month": f"${r.this_month:,.2f}" if r.this_month is not None else None,
            "next_month": f"${r.next_month:,.2f}" if r.next_month is not None else None,
        }
        for r in rows
    ]
    filled_bytes = _fill_form(form_bytes, form_media_type, layout, draw_rows)
    filled_b64 = base64.b64encode(filled_bytes).decode()

    names = ", ".join(r.household_member for r in rows)
    summary = (
        f"Filled in income for {len(rows)} household member(s) ({names}) on page "
        f"{layout['page_index'] + 1} of the form. Double-check the placement before you submit it."
    )
    return FillIncomeTablePdfResponse(pdfBase64=filled_b64, rows=rows, summary=summary)


def fill_liheap_form_from_chart(payload: FillIncomeTablePdfRequest) -> FillFormFromChartResponse:
    """The general entry point: classify the chart first, then route to
    whichever table (income / page 3, or expenses / page 5) it actually
    belongs on, rather than assuming income like the function above."""
    client = get_client()
    chart_media_type, chart_b64 = _parse_image_data_url(payload.chartImage)

    classification = _classify_chart_topic(client, chart_media_type, chart_b64)
    topic = classification.get("topic")
    reason = classification.get("reason") or ""

    if topic not in ("income", "expenses"):
        raise HTTPException(
            status_code=422,
            detail=reason
            or "That chart doesn't look like either a household-income-by-person chart or a "
            "household-expenses-by-category chart — I can only fill in page 3 (income) or page 5 "
            "(expenses) of this form.",
        )

    form_media_type, form_b64 = _parse_form_data_url(payload.formFile)
    form_bytes = base64.b64decode(form_b64)

    income_rows: list[IncomeRow] = []
    expense_rows: list[ExpenseRow] = []
    excluded: list[ExcludedExpenseCategory] = []

    if topic == "income":
        income_rows = _read_income_chart(payload.chartImage)
        layout = _locate_income_table(client, form_media_type, form_b64)
        draw_rows = [
            {
                "household_member": (r.household_member or "")[:28],
                "last_month": f"${r.last_month:,.2f}" if r.last_month is not None else None,
                "this_month": f"${r.this_month:,.2f}" if r.this_month is not None else None,
                "next_month": f"${r.next_month:,.2f}" if r.next_month is not None else None,
            }
            for r in income_rows
        ]
        names = ", ".join(r.household_member for r in income_rows)
        summary = (
            f"This looked like an income chart, so I filled in income for {len(income_rows)} household "
            f"member(s) ({names}) on page {layout['page_index'] + 1}."
        )
    else:
        raw_expenses = _read_expense_chart(payload.chartImage)
        layout = _locate_expense_table(client, form_media_type, form_b64, raw_expenses)
        included = layout.get("included") or []
        excluded_data = layout.get("excluded") or []

        expense_rows = [
            ExpenseRow(
                expense_type=(item.get("label") or item.get("category") or "").strip(),
                amount=item.get("amount"),
            )
            for item in included
            if (item.get("label") or item.get("category"))
        ]
        excluded = [
            ExcludedExpenseCategory(
                category=item.get("category", ""),
                reason=item.get("reason", ""),
            )
            for item in excluded_data
        ]

        if not expense_rows:
            detail = "None of that chart's expense categories looked like ones this form's expenses table accepts."
            if excluded:
                detail += " " + "; ".join(f"{e.category}: {e.reason}" for e in excluded)
            raise HTTPException(status_code=422, detail=detail)

        draw_rows = [
            {
                "expense_type": (r.expense_type or "")[:32],
                "amount": f"${r.amount:,.2f}" if r.amount is not None else None,
            }
            for r in expense_rows
        ]
        summary = (
            f"This looked like an expenses chart, so I filled in {len(expense_rows)} expense(s) on page "
            f"{layout['page_index'] + 1}."
        )
        if excluded:
            plural = "y" if len(excluded) == 1 else "ies"
            summary += (
                f" Left out {len(excluded)} categor{plural} that don't look like LIHEAP-recognized "
                f"expenses: {', '.join(e.category for e in excluded)}."
            )

    filled_bytes = _fill_form(form_bytes, form_media_type, layout, draw_rows)
    filled_b64 = base64.b64encode(filled_bytes).decode()
    summary += " Double-check the placement before you submit it."

    return FillFormFromChartResponse(
        section=topic,
        pdfBase64=filled_b64,
        incomeRows=income_rows,
        expenseRows=expense_rows,
        excludedExpenseCategories=excluded,
        summary=summary,
    )
