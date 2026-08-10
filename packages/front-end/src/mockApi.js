/**
 * mockApi.js — fake backend so you can build/test the frontend before
 * your teammate's FastAPI + Claude Vision pipeline is ready.
 *
 * IMPORTANT: agree on this response shape with your backend teammate
 * EARLY (like, in the first hour). This file IS that agreement, written
 * down. When the real backend is ready, you delete this file and swap
 * the two functions below for real fetch() calls — nothing else in the
 * app needs to change if the shapes match.
 */

// Shared with the static <PieChart> preview (see PieChart.jsx) so the
// visual on screen always matches the numbers the mock backend returns.
export const DEMO_PIE_SERIES = [
  { label: 'Housing', value: 35 },
  { label: 'Food', value: 20 },
  { label: 'Transport', value: 15 },
  { label: 'Savings', value: 20 },
  { label: 'Other', value: 10 },
]

// Every chart the mock backend knows about. Each entry has both a full
// `description` and a `shortDescription` — the chat's "Quick summary /
// Full detail" toggle just switches between the two, so keep the short
// one to a sentence or two.
export const MOCK_CHARTS = {
  bar: {
    description:
      "This is a bar chart titled 'Monthly Rainfall'. It shows rainfall in millimeters across five months: January at 40mm, February at 65mm, March at 30mm, April at 90mm, and May at 55mm. April has the highest rainfall and March has the lowest.",
    shortDescription:
      "Bar chart: Monthly Rainfall. April had the most rain at 90mm, March the least at 30mm.",
    // The structured data is what makes follow-up Q&A fast and accurate —
    // the backend queries against this instead of re-reading the image
    // for every single question.
    structuredData: {
      chartType: 'bar',
      title: 'Monthly Rainfall',
      xLabel: 'Month',
      yLabel: 'Rainfall (mm)',
      unit: 'mm',
      series: [
        { label: 'January', value: 40 },
        { label: 'February', value: 65 },
        { label: 'March', value: 30 },
        { label: 'April', value: 90 },
        { label: 'May', value: 55 },
      ],
    },
  },
  pie: {
    description:
      "This is a pie chart titled 'Household Budget'. It breaks monthly spending into five categories: Housing at 35%, Food at 20%, Transport at 15%, Savings at 20%, and Other at 10%. Housing takes up the largest share of the budget, and Transport the smallest.",
    shortDescription:
      "Pie chart: Household Budget. Housing is the largest share at 35%, Transport the smallest at 15%.",
    structuredData: {
      chartType: 'pie',
      title: 'Household Budget',
      unit: '%',
      series: DEMO_PIE_SERIES,
    },
  },
}

// Simulates: POST /analyze-chart  (body: { image: base64String })
// Returns a structured description + the extracted data the backend
// would send back after running the image through Claude Vision.
export async function analyzeChart(imageDataUrl) {
  await delay(1500) // pretend network latency
  return MOCK_CHARTS.bar
}

// Simulates the same endpoint, but for the built-in "try a sample chart"
// demo button — lets you show off / test the full flow with no camera
// or file upload needed. No image is actually sent anywhere; this is
// just a canned response with a shorter fake delay.
export async function analyzeDemoChart() {
  await delay(700)
  return MOCK_CHARTS.pie
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
