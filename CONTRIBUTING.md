# Contributing / Team split

This app is already split into files with clear boundaries. If everyone
stays inside their own file(s) below, most of you can work in parallel
without touching the same lines — which is what actually avoids merge
conflicts (not being "careful," just not editing the same file).

## Who owns what

| Area | File(s) | Notes |
|---|---|---|
| **Backend integration** | [`src/mockApi.js`](src/mockApi.js) | The seam. Swap `analyzeChart`, `analyzeDemoChart`, and `askQuestion` for real `fetch()` calls once the backend's up. Keep the return shapes identical (see the comment at the top of the file) and nothing else in the app needs to change. |
| **Camera / upload flow** | [`src/components/CameraCapture.jsx`](src/components/CameraCapture.jsx) | Camera access, file upload, the "try a sample chart" demo button, the captured-image preview. |
| **Chat / voice UI** | [`src/components/ChatPanel.jsx`](src/components/ChatPanel.jsx) | Message list, suggested-question flow, depth toggle, speech controls (stop/repeat/rate), transcript copy. |
| **Speech hook** | [`src/hooks/useSpeech.js`](src/hooks/useSpeech.js) | Wraps SpeechRecognition + speechSynthesis. One instance is created in `App.jsx` and passed down as props — don't call `useSpeech()` again elsewhere, or "Stop speaking" etc. will only affect whichever instance fired it. |
| **Chart visual** | [`src/components/PieChart.jsx`](src/components/PieChart.jsx) | Self-contained SVG pie chart, takes `series`/`size`/`showLabels`. Pure presentational — no state. |
| **Styling** | [`src/index.css`](src/index.css) | Shared by everyone, so it's the most likely conflict spot. **Append your new rules near the section for the component you're touching** (the file's already organized that way — header, layout, camera, chat, etc.) rather than reordering existing rules. |
| **State wiring / layout** | [`src/App.jsx`](src/App.jsx) | Owns top-level state and passes props down to the two panels. Smallest file, but the one most likely to cause conflicts if two people edit it at once — see below. |

## The one file to coordinate on: `App.jsx`

Everything else is isolated by props. `App.jsx` is the spine that wires
it all together, so:

- If you need a new piece of state or a new prop passed to
  `CameraCapture` or `ChatPanel`, **add** it — don't rename or reorder
  existing props, since that breaks whoever else is also reading them.
- If two people need to touch `App.jsx` in the same work session, say so
  in your team chat first, or do it as one quick pairing pass instead of
  two parallel edits.

## The contract that matters most

If you're working on the backend (`mockApi.js`), the shape your real
endpoints need to return is documented in the comment block at the top
of that file and in the README's "Connecting to the real backend"
section. Agree on that shape with whoever's touching `App.jsx` *before*
you build the endpoint — that's the one piece of coordination that
actually matters; everything else is independent.

## Suggested git workflow

1. Don't commit straight to `main`. Branch per feature:
   ```bash
   git checkout -b your-name/camera-flow
   ```
2. Push your branch and open a pull request into `main` on GitHub
   instead of pushing directly — even for a hackathon, it gives
   everyone a chance to glance at what changed before it lands.
3. Pull `main` and rebase (or merge) before you open the PR, so you're
   not the one resolving a pile of conflicts at the end:
   ```bash
   git fetch origin
   git rebase origin/main
   ```
4. Since almost everyone's changes live in different files, most PRs
   should merge cleanly with no manual conflict resolution at all.
