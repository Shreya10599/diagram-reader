"""app/form_schema_extraction.py — reads a user-uploaded form (PDF or a
photo of one) and extracts its fillable-field schema at runtime.

This is a real pivot away from the rest of this app's form-filling design:
form_schemas.py's whole premise was "a known, fixed target form doesn't
need an agent to infer its structure" — true for LIHEAP/stock_basis/
vera_summary, where the app assumes every user needs the same form. Once
the user can upload ANY form, that assumption is gone, and figuring out
what fields exist — and which of them are numbers a chart could plausibly
supply versus identity/date/checkbox fields that can't be — genuinely
requires reading and understanding an unfamiliar document. That's not
something a lookup table can do, so this is a real second agentic step in
the overall flow, alongside validate_mapping: this one decides the
STRUCTURE of the problem (what fields exist, computedAnswer vs manual),
validate_mapping decides whether one specific mapped VALUE is trustworthy.
Different judgments, both genuinely needed.

Once extracted, the fields feed straight into
FormFillRequest(formType="custom", customSchema=fields) — see
form_graph.py's select_form_schema_node — so nothing about map_to_schema,
validate_mapping, or record_field_result needs to change; they already
operate generically on "a schema," wherever it came from.
"""

import base64
import logging
import re

from fastapi import HTTPException

from .config import CLAUDE_MODEL
from .llm_agent import _extract_json, get_client
from .models import FormSchemaExtractionRequest, FormSchemaExtractionResponse

logger = logging.getLogger("diagram_reader")


EXTRACT_FORM_SCHEMA_SYSTEM_PROMPT = """You are reading a real government or institutional form — \
provided as a PDF document or a photo of one — to build a list of its fillable fields, so a \
chart-reading assistant can later help fill in whichever fields are numeric figures a chart of the \
applicant's own data (energy usage, income, prices, etc.) could plausibly supply.

For EVERY simple, single-value field on the form — a single blank, box, or line asking for one piece \
of information (e.g. "Applicant Name", "Date of Birth", "Estimated Average Monthly Usage (kWh)", \
"Account Number") — report it with:
- field_id: a short snake_case identifier, unique within this form
- label: the field's exact label/question text as it appears on the form
- unit: the unit shown on the form for this field, if any (e.g. "kWh", "$"), else an empty string
- source: "computedAnswer" if this is a NUMERIC figure that a chart of the applicant's own data could \
plausibly supply — "manual" for anything else (names, dates, addresses, ID numbers, checkboxes, \
yes/no questions, signatures — anything that isn't a number a chart could derive)
- reason: one short phrase explaining that classification

Skip any section that is a repeating TABLE with multiple rows of the same kind (e.g. a table listing \
several household members, or several expense line items) — those can't be represented as a flat field \
list. Instead, list the title of each such section in "skippedSections" so nothing is silently dropped \
from the form without the caller knowing.

If the document has multiple pages, look at all of them. Use the form's own header/title text for \
"formTitle".

Respond with ONLY a single JSON object (no markdown fences, no commentary outside the JSON), matching \
exactly this shape:

{
  "formTitle": "<the form's title/name>",
  "fields": [
    { "field_id": "...", "label": "...", "unit": "...", "source": "computedAnswer" | "manual", \
"reason": "..." },
    ...
  ],
  "skippedSections": ["<section title>", ...]
}"""


def _parse_form_data_url(data_url: str) -> tuple[str, str]:
    """Like llm_agent._parse_image_data_url, but also accepts
    application/pdf, not just image/* — the uploaded form can be either."""
    match = re.match(r"^data:([\w.+-]+/[\w.+-]+);base64,(.+)$", data_url, re.DOTALL)
    if not match:
        raise HTTPException(
            status_code=400,
            detail="formFile must be a data URL like 'data:application/pdf;base64,...' "
            "or 'data:image/png;base64,...'",
        )
    media_type, payload = match.group(1), match.group(2)
    try:
        base64.b64decode(payload, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid base64 form data: {exc}")
    return media_type, payload


def extract_form_schema(payload: FormSchemaExtractionRequest) -> FormSchemaExtractionResponse:
    media_type, base64_data = _parse_form_data_url(payload.formFile)
    client = get_client()

    if media_type == "application/pdf":
        # Sent as a native PDF document block — Claude converts each page
        # to text + image internally, no local PDF rendering needed on
        # this end (deliberately avoids the poppler/pdftoppm dependency
        # this session kept running into for local PDF handling).
        content_block = {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": base64_data},
        }
    elif media_type.startswith("image/"):
        content_block = {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": base64_data},
        }
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported form file type {media_type!r} — upload a PDF or a photo of the form.",
        )

    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            # A real multi-page form (SFN 529 is 3 pages of fields alone)
            # produces a lot of output once every field gets an id/label/
            # unit/source/reason, and adaptive thinking tokens count
            # against this same budget — 4000 was cutting off mid-JSON on
            # real forms. 12000 mirrors the headroom the other extraction
            # call in llm_agent.py needed for the same reason.
            max_tokens=12000,
            thinking={"type": "adaptive"},
            system=EXTRACT_FORM_SCHEMA_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        content_block,
                        {
                            "type": "text",
                            "text": "Extract this form's field schema per your instructions.",
                        },
                    ],
                }
            ],
            output_config={"effort": "medium"},
        )
    except Exception as exc:
        logger.exception("Form schema extraction call failed")
        raise HTTPException(status_code=502, detail=f"Form schema extraction failed: {exc}")

    if response.stop_reason == "max_tokens":
        raise HTTPException(
            status_code=502,
            detail="Form schema extraction response was cut off (hit max_tokens) before finishing.",
        )

    raw_text = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    )
    if not raw_text.strip():
        raise HTTPException(status_code=502, detail="Form schema extraction produced no output.")

    parsed = _extract_json(raw_text)

    try:
        result = FormSchemaExtractionResponse(**parsed)
    except Exception as exc:
        logger.error(
            "Form schema extraction JSON didn't match expected schema: %s\nParsed: %s", exc, parsed
        )
        raise HTTPException(
            status_code=502,
            detail=f"Form schema extraction response didn't match the expected schema: {exc}",
        )

    if not result.fields:
        raise HTTPException(
            status_code=422,
            detail="Couldn't find any fillable fields on this form — try a clearer scan/photo, "
            "or a different page if this is a multi-page form.",
        )

    return result
