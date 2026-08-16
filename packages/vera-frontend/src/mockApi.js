/**
 * mockApi.js — fake backend so the frontend works end-to-end before the
 * real FastAPI + Claude Vision pipeline is ready.
 *
 * IMPORTANT: agree on this response shape with your backend teammate
 * EARLY. This file IS that agreement, written down. When the real
 * backend is ready, delete this file and swap the two functions below
 * for real fetch() calls — nothing else in the app needs to change if
 * the shapes match.
 *
 * Shape of what analyzeSource() must return:
 *   {
 *     fields: { name, address, min, max, average },   // strings, ready to drop into the form
 *     summary: string,                                 // one or two sentences, for "Tell me about it"
 *   }
 */

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

// Simulates: POST /analyze  (body: { image: base64String } or { link: string })
// `source` is either a data URL (photo/upload) or a pasted link string —
// the real backend will branch on that the same way. Returns the fields
// to drop into the form plus a short plain-language summary.
export async function analyzeSource(source) {
  await delay(1500) // pretend network latency
  return MOCK_RESULT
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

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}
