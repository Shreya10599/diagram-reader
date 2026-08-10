# Diagram Reader — Frontend Starter

A React + Vite starter for the camera → chart description → follow-up
Q&A flow. Built with a mock backend so you can start working immediately,
without waiting on the FastAPI/Claude Vision pipeline.

## Setup

You'll need [Node.js](https://nodejs.org) installed (v18+). Then:

```bash
cd diagram-reader
npm install
npm run dev
```

This opens the app at `http://localhost:5173`. Open it in **Chrome** —
the Web Speech API (voice input) has the best support there.

To test on your phone (recommended, since you'll want to test the real
camera): make sure your phone and laptop are on the same wifi network,
then visit `http://<your-laptop-ip>:5173` on the phone. Run `ipconfig`
(Windows) or `ifconfig`/`ipconfig getifaddr en0` (Mac) to find your IP.

## What's already built

- **`CameraCapture.jsx`** — opens the camera, lets the student snap a
  photo or upload one as a fallback. Hands a base64 image up to `App.jsx`.
- **`ChatPanel.jsx`** — the conversation UI. Shows messages, reads
  answers aloud automatically, supports typed or spoken follow-up
  questions. Has proper ARIA labeling throughout.
- **`useSpeech.js`** — hook wrapping speech-to-text and text-to-speech
  so you don't have to touch the raw browser APIs directly.
- **`mockApi.js`** — fake backend responses so the whole flow works
  end-to-end right now, before the real backend exists.

## Connecting to the real backend

Once your backend teammate has an endpoint ready, open `mockApi.js` —
it's the single file to change. Replace the two functions with real
`fetch()` calls, e.g.:

```js
export async function analyzeChart(imageDataUrl) {
  const res = await fetch('http://localhost:8000/analyze-chart', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ image: imageDataUrl }),
  })
  return res.json() // must return { description, structuredData }
}

export async function askQuestion(question, structuredData) {
  const res = await fetch('http://localhost:8000/ask-question', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, structuredData }),
  })
  const data = await res.json()
  return data.answer
}
```

**Important:** look at the shape documented
at the top of `mockApi.js`. As long as
the real responses match that shape, you won't need to touch `App.jsx`
or `ChatPanel.jsx` at all when you swap it in.

## If something breaks

- **Camera won't open**: browsers block camera access on non-HTTPS,
  non-localhost origins. `localhost` is fine for dev; if you deploy to
  Vercel it'll get HTTPS automatically.
- **Voice input does nothing**: Safari's SpeechRecognition support is
  unreliable. Demo on Chrome. The typed-question fallback always works
  regardless.
- **`npm install` fails**: make sure you're on Node 18+. Run `node -v`
  to check.


