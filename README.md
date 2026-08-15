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

`mockApi.js` is the single file that talks to the backend.

- **`analyzeChart`** — done. Calls the real `POST /analyze-chart` with a
  `fetch()`, sends `{ image: base64DataUrl }`, and returns whatever the
  backend sends back. Points at `http://localhost:8000` by default;
  override with `VITE_API_BASE_URL` in a `.env` in `packages/front-end`
  if your backend runs elsewhere (e.g. once it's deployed on Render).
- **`askQuestion`** — still mocked. There's no `/ask-question` endpoint
  on the backend yet (see task list below). Once it exists, swap it the
  same way `analyzeChart` was swapped.
- **"Try a sample chart" demo button** — also hits the real backend now.
  `App.jsx`'s `handleDemo` generates a real chart image client-side
  (`utils/sampleChart.js` renders the pie data as SVG, then rasterizes it
  to a PNG via canvas) and sends it through the same real `analyzeChart()`
  call a camera capture would use — so the one-click demo path still
  proves the actual Claude pipeline works, without needing a live camera
  or wifi during a demo. The generated image is also what's shown as the
  preview, so what's on screen matches exactly what was analyzed.

**Important:** as long as the real response shape matches what's
documented at the top of `mockApi.js`, you won't need to touch
`App.jsx` or `ChatPanel.jsx` when wiring up new endpoints.

## If something breaks

- **Camera won't open**: browsers block camera access on non-HTTPS,
  non-localhost origins. `localhost` is fine for dev; if you deploy to
  Vercel it'll get HTTPS automatically.
- **Voice input does nothing**: Safari's SpeechRecognition support is
  unreliable. Demo on Chrome. The typed-question fallback always works
  regardless.
- **`npm install` fails**: make sure you're on Node 18+. Run `node -v`
  to check.


## How to run the Fast API Backend:
- Go to `packages/back-end`
- Create a venv if you haven't: `python3 -m venv .venv`
- Install deps: `.venv/bin/pip install -r requirements.txt`
- Copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY` (get one from
  console.anthropic.com — don't commit `.env`, it's gitignored)
- Run: `.venv/bin/uvicorn app.main:app --reload`
- The API is now at `http://localhost:8000`. `/health` should return
  `{"status": "ok"}`. `/analyze-chart` takes `{ "image": "data:image/png;base64,..." }`
  and calls Claude (`CLAUDE_MODEL` in `.env`, defaults to `claude-sonnet-5`) to
  extract chart data.

### What's implemented so far

- `/analyze-chart`: single Claude Vision call with a prompt that walks the
  model through axis calibration (anchor to labeled gridlines before reading
  values) and a self-check pass, then returns `description`, `shortDescription`,
  and `structuredData` matching the shape `mockApi.js` already documents on the
  front-end. Response also includes `confidence` and `uncertainValues` fields
  (not consumed by the front-end yet) for a future correction UI.

### Measuring extraction accuracy

`packages/back-end/scripts/eval_accuracy.py` generates a few charts with
*known* values (bar/line/pie), runs each through the real `/analyze-chart`
several times, and reports pass rate + mean error against ground truth —
saved as JSON + a markdown summary in `scripts/eval_results/`. Run it with
the backend already running:

```bash
cd packages/back-end
.venv/bin/pip install -r requirements.txt   # picks up matplotlib/requests
.venv/bin/python scripts/eval_accuracy.py
```

Commit the output files — they're the actual evidence extraction accuracy
was measured, worth citing directly in a README/pitch ("validated against
N known-value charts across 3 chart types, X% within 5% of ground truth").

### Not yet implemented (next steps)

- **Separate verification pass**: right now accuracy relies on the model
  self-checking within one call. The planned next step is a second Claude
  call that's shown the image *and* the first pass's extracted table, and
  asked only to confirm/correct — cheaper to get right than one call doing
  everything at once.
- **`/ask-question`**: front-end already calls this; not built yet. Should
  answer strictly from the `structuredData` already extracted, not re-query
  the image, so answers stay consistent across follow-ups.
- Swap `mockApi.js`'s two functions for real `fetch()` calls once ready
  (instructions already in `packages/front-end/README.md` — no other
  front-end changes needed if the response shape matches).