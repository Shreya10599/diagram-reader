/**
 * mockApi.js — was the fake backend so the frontend worked end-to-end
 * before the real FastAPI + Claude Vision pipeline was ready.
 *
 * STATUS: `analyzeSource` now calls the real backend (POST /vera/analyze —
 * see packages/back-end/app/vera.py) for photo/upload sources (data URLs).
 * The "Add a link" path still returns MOCK_RESULT: the backend doesn't
 * fetch arbitrary URLs server-side yet (that needs SSRF-safe validation —
 * only allow image content-types, size limits, no private/internal IPs —
 * deliberately deferred). `askQuestion` is also still mocked, but now
 * answers from REAL fields once a chart's actually been analyzed, since
 * its three canned questions (highest/lowest/mean) just read off
 * fields.min/max/average rather than needing their own backend call.
 *
 * Shape analyzeSource() returns (must match VeraAnalyzeResponse in
 * packages/back-end/app/models.py):
 *   {
 *     fields: { name, address, min, max, average },   // strings, ready to drop into the form
 *     summary: string,                                 // one or two sentences, for "Tell me about it"
 *   }
 */

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

const MOCK_RESULT = {
  fields: {
    name: 'Jane Doe',
    address: '123 Main St, Springfield',
    min: '30',
    max: '90',
    average: '60',
  },
  summary:
    "Your chart's values range from 30 up to 90, averaging 60 overall.",
}

// Real call (photo/upload): POST /vera/analyze  (body: { image: base64String })
// Mocked call (link): `source` is a pasted link string instead of a data
// URL — see the file header for why that path isn't wired to the real
// backend yet.
export async function analyzeSource(source) {
  const isDataUrl = typeof source === 'string' && source.startsWith('data:')

  if (!isDataUrl) {
    await delay(1500) // pretend network latency for the still-mocked link path
    return MOCK_RESULT
  }

  const res = await fetch(`${API_BASE_URL}/vera/analyze`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ image: source }),
  })

  if (!res.ok) {
    // FastAPI's HTTPException responses look like { detail: "..." } —
    // surface that message (bad image, no data points found, Claude
    // error) instead of a generic failure, since VeraAssistant's
    // runAnalysis catch block speaks/shows whatever this throws.
    const errorBody = await res.json().catch(() => null)
    throw new Error(errorBody?.detail ?? `Chart analysis failed (${res.status})`)
  }

  return res.json()
}

// Simulates: POST /ask-question  (body: { question, fields })
// Only three canned questions are offered in the UI right now (no free
// typing), so this is intentionally simple keyword matching — replace
// with the real backend call once it exists.
export async function askQuestion(question, fields) {
  await delay(600)

  if (!fields) {
    return "Add a chart first — once VERA's read it, I can answer questions about it."
  }

  const q = question.toLowerCase()

  if (q.includes('highest')) {
    return `The highest value is ${fields.max}.`
  }
  if (q.includes('lowest')) {
    return `The lowest value is ${fields.min}.`
  }
  if (q.includes('mean')) {
    return `It means your values typically land around ${fields.average}, with the widest swing between ${fields.min} and ${fields.max}.`
  }

  return "That's a mock answer — once the real backend is connected, this will query the actual chart data."
}

// Real call: POST /analyze-chart  (body: { image, task? })
// Lower-level than analyzeSource() above (which always runs the fixed
// min/max/average VERA task server-side) — this one takes an arbitrary
// ChartTask, e.g. "compute this specific field's value from this chart."
// Not currently called by VeraAssistant.jsx — its "Upload form" flow is
// just a client-side PDF preview now (see PdfPreview.jsx), independent of
// chart reading entirely. Kept here as a valid wrapper around a real,
// working backend endpoint in case a future UI needs a single targeted
// chart computation.
export async function analyzeChart(image, task) {
  const res = await fetch(`${API_BASE_URL}/analyze-chart`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(task ? { image, task } : { image }),
  })
  if (!res.ok) {
    const errorBody = await res.json().catch(() => null)
    throw new Error(errorBody?.detail ?? `Chart analysis failed (${res.status})`)
  }
  return res.json()
}

// Real call: POST /fill-form
// (body: { formType, customSchema?, computedAnswers, image, extractedSeries })
export async function fillForm({ formType, customSchema, computedAnswers, image, extractedSeries }) {
  const res = await fetch(`${API_BASE_URL}/fill-form`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ formType, customSchema, computedAnswers, image, extractedSeries }),
  })
  if (!res.ok) {
    const errorBody = await res.json().catch(() => null)
    throw new Error(errorBody?.detail ?? `Form filling failed (${res.status})`)
  }
  return res.json()
}

// Real call: POST /extract-form-schema  (body: { formFile })
// `formFile` is a data URL for the uploaded form — either a PDF
// ("data:application/pdf;base64,...") or a photo of one ("data:image/...").
export async function extractFormSchema(formFile) {
  const res = await fetch(`${API_BASE_URL}/extract-form-schema`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ formFile }),
  })
  if (!res.ok) {
    const errorBody = await res.json().catch(() => null)
    throw new Error(errorBody?.detail ?? `Reading that form failed (${res.status})`)
  }
  return res.json()
}

// Real call: POST /liheap/fill-income-table-pdf
// (body: { chartImage, formFile }) -> { pdfBase64, rows, summary }
// `chartImage` is the ONE grouped household-income chart (member x
// last/this/next month); `formFile` is the LIHEAP form itself (PDF or a
// photo of one). Returns an actual filled copy of the form's income table
// as a downloadable PDF, not a JSON worksheet. Not currently called by
// VeraAssistant.jsx — "Upload form" there is a client-side PDF preview
// now, independent of chart reading (see PdfPreview.jsx) — but the
// backend endpoint is real and working, for whenever a UI wants it.
export async function fillIncomeTablePdf({ chartImage, formFile }) {
  const res = await fetch(`${API_BASE_URL}/liheap/fill-income-table-pdf`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ chartImage, formFile }),
  })
  if (!res.ok) {
    const errorBody = await res.json().catch(() => null)
    throw new Error(errorBody?.detail ?? `Filling that form failed (${res.status})`)
  }
  return res.json()
}

// Real call: POST /liheap/fill-form-from-chart
// (body: { chartImage, formFile }) -> { section, pdfBase64, incomeRows,
// expenseRows, excludedExpenseCategories, summary }
// The general version of fillIncomeTablePdf above: the chart can be EITHER
// an income chart or an expenses chart — the backend classifies it and
// fills page 3 (income) or page 5 (expenses) accordingly. Same two-field
// request shape. Not currently called by VeraAssistant.jsx (see the note
// on fillIncomeTablePdf above — same reason), but this is the one to
// reach for first if/when the UI wants real PDF-filling back: it
// subsumes fillIncomeTablePdf's income-only case and also handles
// expenses.
export async function fillFormFromChart({ chartImage, formFile }) {
  const res = await fetch(`${API_BASE_URL}/liheap/fill-form-from-chart`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ chartImage, formFile }),
  })
  if (!res.ok) {
    const errorBody = await res.json().catch(() => null)
    throw new Error(errorBody?.detail ?? `Filling that form failed (${res.status})`)
  }
  return res.json()
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}
