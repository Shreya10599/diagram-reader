"""app/form_schemas.py — hardcoded target-form field schemas.

Per the Simplify-autofill lesson from planning: a known, fixed target form
doesn't need an agent to infer its structure — a deterministic mapping is
strictly more accurate than any model guessing field names, and cheaper.
So the two forms this hackathon supports (LIHEAP, inherited-stock basis)
are hardcoded here, not discovered. The agentic budget in this pipeline is
spent on validate_mapping (deciding whether a mapped value is trustworthy
enough to put in front of the user), not on schema discovery.

Adding a third form later means adding an entry here, not writing a new
discovery agent — that's the whole point of the design.
"""

from typing import Literal, Optional

from pydantic import BaseModel


class FormField(BaseModel):
    """One field in a target form/worksheet.

    `source` says where this field's value comes from:
      - "computedAnswer": filled from the chart pipeline's computedAnswer
        (e.g. LIHEAP's average monthly usage, stock basis's high/low
        average) — this is the only kind of field validate_mapping looks
        at, since it's the only kind an agent can meaningfully judge.
      - "manual": the chart pipeline has no way to know this (applicant
        name, account number, date of death) — map_to_schema marks these
        straight as needing manual entry, no agent involved, because
        there's nothing here to validate or get wrong.
    """

    field_id: str
    label: str
    unit: Optional[str] = ""
    required: bool = True
    source: Literal["computedAnswer", "manual"]


FORM_SCHEMAS: dict[str, list[FormField]] = {
    "liheap": [
        FormField(
            field_id="average_monthly_usage_kwh",
            label="Estimated Average Monthly Usage",
            unit="kWh",
            source="computedAnswer",
        ),
        FormField(
            field_id="applicant_name",
            label="Applicant Name",
            source="manual",
        ),
        FormField(
            field_id="account_number",
            label="Utility Account Number",
            source="manual",
        ),
        FormField(
            field_id="service_address",
            label="Service Address",
            source="manual",
        ),
    ],
    "stock_basis": [
        FormField(
            field_id="date_of_death",
            label="Date of Death",
            source="manual",
        ),
        FormField(
            field_id="reportable_basis_per_share",
            label="Reportable Basis (average of high/low on date of death)",
            unit="$",
            source="computedAnswer",
        ),
    ],
    # VERA's landing form (packages/vera-frontend) — unlike LIHEAP/stock_basis,
    # this one needs THREE computedAnswer-sourced fields validated at once
    # (min, max, average), which is why FormFillRequest carries a list of
    # ComputedAnswers instead of one. field_ids here ("min"/"max"/"average")
    # match VERA's `fields` object keys exactly, on purpose, so app/vera.py
    # can build the response with zero translation.
    "vera_summary": [
        FormField(
            field_id="name",
            label="Name",
            source="manual",
        ),
        FormField(
            field_id="address",
            label="Address",
            source="manual",
        ),
        FormField(
            field_id="min",
            label="Minimum value",
            source="computedAnswer",
        ),
        FormField(
            field_id="max",
            label="Maximum value",
            source="computedAnswer",
        ),
        FormField(
            field_id="average",
            label="Average",
            source="computedAnswer",
        ),
    ],
}


def get_schema(form_type: str) -> list[FormField]:
    schema = FORM_SCHEMAS.get(form_type)
    if schema is None:
        raise ValueError(
            f"Unknown formType {form_type!r} — supported: {list(FORM_SCHEMAS)}"
        )
    return schema
