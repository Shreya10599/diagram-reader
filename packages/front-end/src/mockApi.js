/**
 * mockApi.js — was the fake backend for building/testing the front-end
 * before the FastAPI + Claude Vision pipeline existed.
 *
 * STATUS: `analyzeChart` now calls the real `/analyze-chart` endpoint —
 * this is also what the "try a sample chart" demo button calls now (see
 * App.jsx's handleDemo + utils/sampleChart.js), with a generated image
 * instead of a camera capture. Only `askQuestion` is still mocked — there's
 * no real /ask-question endpoint on the backend yet. Swap it the same way
 * once it exists server-side.
 */

// Base URL of the FastAPI backend. Override by setting VITE_API_BASE_URL
// in a .env file in packages/front-end if you're not running it on the
// default port.
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

// Feeds both the small icon preview on the "Try a sample chart" button
// (PieChart.jsx, purely decorative) and utils/sampleChart.js, which
// rasterizes this same data into the actual image sent to the backend —
// so the numbers a judge hears back are always these numbers.
export const DEMO_PIE_SERIES = [
  { label: 'Housing', value: 35 },
  { label: 'Food', value: 20 },
  { label: 'Transport', value: 15 },
  { label: 'Savings', value: 20 },
  { label: 'Other', value: 10 },
]

// Real call: POST /analyze-chart  (body: { image: base64String, task?: ChartTask })
// Sends the captured/uploaded image to the FastAPI backend, which calls
// Claude Vision and returns { description, shortDescription, structuredData,
// computedAnswer }. `task` is optional — omit it for a plain description: with
// no task, the backend always sets computedAnswer to null (see
// EXTRACTION_SYSTEM_PROMPT step 9). Pass one when you need a specific derived
// figure, e.g. the form-filling flow re-calls this with a task scoped to
// exactly the figure the target form needs (see App.jsx's handleFillForm).
export async function analyzeChart(imageDataUrl, task) {
  const res = await fetch(`${API_BASE_URL}/analyze-chart`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(task ? { image: imageDataUrl, task } : { image: imageDataUrl }),
  })

  if (!res.ok) {
    // FastAPI's HTTPException responses look like { detail: "..." } —
    // surface that message (e.g. missing API key, bad image, Claude
    // error) instead of a generic failure, since App.jsx speaks/alerts
    // whatever this throws.
    const errorBody = await res.json().catch(() => null)
    throw new Error(errorBody?.detail ?? `Chart analysis failed (${res.status})`)
  }

  return res.json()
}

// Real call: POST /fill-form  (body: { formType, computedAnswer, image, extractedSeries })
// Maps a chart's computedAnswer onto a target form's hardcoded field
// schema and runs validate_mapping (the backend's one agentic step) on
// each mapped value, returning a worksheet — never auto-submitted
// anywhere, just filled in for the user to review and copy themselves.
export async function fillForm({ formType, computedAnswer, image, extractedSeries }) {
  const res = await fetch(`${API_BASE_URL}/fill-form`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ formType, computedAnswer, image, extractedSeries }),
  })

  if (!res.ok) {
    const errorBody = await res.json().catch(() => null)
    throw new Error(errorBody?.detail ?? `Form filling failed (${res.status})`)
  }

  return res.json()
}

// Simulates: POST /ask-question  (body: { question, structuredData })
export async function askQuestion(question, structuredData) {
  await delay(800)

  // No chart analyzed yet — this is also the seam where a general-purpose
  // backend agent (not tied to a specific chart) would take over.
  if (!structuredData) {
    return "Upload a photo, capture one with your camera, or try the sample chart first — once there's a chart to look at, I can answer questions about it."
  }

  // Extremely dumb keyword-matching just so the demo responds sensibly
  // while you're building. Replace with the real backend call.
  const q = question.toLowerCase()
  const unit = structuredData.unit ?? ''

  if (q.includes('highest') || q.includes('biggest') || q.includes('largest')) {
    const max = structuredData.series.reduce((a, b) =>
      a.value > b.value ? a : b
    )
    return `The highest value is ${max.label} at ${max.value}${unit}.`
  }
  if (q.includes('lowest') || q.includes('smallest')) {
    const min = structuredData.series.reduce((a, b) =>
      a.value < b.value ? a : b
    )
    return `The lowest value is ${min.label} at ${min.value}${unit}.`
  }

  return "That's a mock answer — once the real backend is connected, this will query the actual chart data."
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}
