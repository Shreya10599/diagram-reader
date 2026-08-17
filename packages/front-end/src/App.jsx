import { useState, useCallback } from 'react'
import CameraCapture from './components/CameraCapture.jsx'
import ChatPanel from './components/ChatPanel.jsx'
import { useSpeech } from './hooks/useSpeech.js'
import { analyzeChart, askQuestion, fillForm, DEMO_PIE_SERIES } from './mockApi.js'
import { generateSampleChartImage } from './utils/sampleChart.js'

// One ChartTask per form_schemas.py's computedAnswer-sourced field — a
// plain "analyze this chart" call has no task, so computedAnswer always
// comes back null (see EXTRACTION_SYSTEM_PROMPT step 9). Filling a
// specific form needs a specific derived figure from the same chart, so
// handleFillForm below re-runs analysis with the matching task rather
// than reusing whatever (if anything) came back from the original call.
const FORM_TASKS = {
  liheap: {
    type: 'summary',
    instruction:
      'Compute the average monthly energy usage across all months/periods shown in this chart, in kWh (convert to kWh and show the conversion in the formula if the chart uses a different unit)',
  },
  stock_basis: {
    type: 'lookup',
    instruction:
      'Find the high and low price on the specified date and average them to get the reportable per-share cost basis',
  },
}

/**
 * App — top-level state.
 *
 * Layout is two panels shown side by side (stacked on mobile):
 *   - the chart panel (camera / upload / sample-chart demo)
 *   - the chat panel, which is ALWAYS visible so it's easy to wire up
 *     a general backend agent later, not just chart Q&A.
 *
 * useSpeech() is called once, here, rather than separately in each
 * component — that way "Stop speaking", "Repeat", and the rate control
 * in ChatPanel all act on the exact same speech session as the status
 * announcements fired from the capture flow below.
 *
 * Swap mockApi.js for real fetch() calls to your FastAPI backend once
 * it's ready — everything else stays the same as long as the response
 * shapes match what's documented in mockApi.js.
 */
export default function App() {
  const [description, setDescription] = useState('')
  const [shortDescription, setShortDescription] = useState('')
  const [structuredData, setStructuredData] = useState(null)
  // The targeted-computation answer (e.g. LIHEAP average monthly usage) —
  // only present when the analysis included a ChartTask. This is what
  // gets mapped onto a form's fields by handleFillForm below; if it's
  // null, there's nothing a worksheet could fill in from the chart.
  const [computedAnswer, setComputedAnswer] = useState(null)
  const [capturedImage, setCapturedImage] = useState(null) // data URL from camera, upload, or the generated sample chart
  const [isAnalyzing, setIsAnalyzing] = useState(false)

  const {
    isListening,
    transcript,
    startListening,
    stopListening,
    isSpeaking,
    speak,
    stopSpeaking,
    rate,
    setRate,
  } = useSpeech()

  const runAnalysis = useCallback(
    async (analyzeFn, previewImage) => {
      setIsAnalyzing(true)
      setCapturedImage(previewImage)
      try {
        const result = await analyzeFn()
        // Announce completion right away — the chart description itself
        // is only spoken once the student asks for it (see ChatPanel's
        // suggested-question flow), but "done analyzing" is immediate.
        speak('Analysis complete')
        setDescription(result.description)
        setShortDescription(result.shortDescription)
        setStructuredData(result.structuredData)
        setComputedAnswer(result.computedAnswer ?? null)
      } catch (err) {
        console.error('Analysis failed:', err)
        // Surface the real reason (e.g. missing API key, Claude API error,
        // bad image format) instead of a generic message — makes this
        // debuggable from the alert alone instead of requiring devtools.
        const errorMsg = `Something went wrong analyzing the image: ${err.message || err}`
        speak(errorMsg)
        alert(errorMsg)
      } finally {
        setIsAnalyzing(false)
      }
    },
    [speak]
  )

  const handleCapture = useCallback(
    (imageDataUrl) => {
      // Only the real camera/upload path counts as "a photo" — the demo
      // button below skips this announcement since nothing was captured.
      speak('Photo captured, analyzing now')
      runAnalysis(() => analyzeChart(imageDataUrl), imageDataUrl)
    },
    [runAnalysis, speak]
  )

  const handleDemo = useCallback(async () => {
    // Generates a real chart image client-side (see utils/sampleChart.js)
    // and runs it through the exact same analyzeChart() call a camera
    // capture or upload would — so "try a sample chart" still exercises
    // the real Claude pipeline instead of returning canned data. The
    // generated data URL doubles as the on-screen preview, so what's
    // shown is exactly what was analyzed.
    try {
      const dataUrl = await generateSampleChartImage(DEMO_PIE_SERIES, 'Household Budget')
      runAnalysis(() => analyzeChart(dataUrl), dataUrl)
    } catch (err) {
      console.error('Sample chart generation failed:', err)
      const errorMsg = 'Could not generate the sample chart. Try again.'
      speak(errorMsg)
      alert(errorMsg)
    }
  }, [runAnalysis, speak])

  const handleAskQuestion = useCallback(
    async (question) => askQuestion(question, structuredData),
    [structuredData]
  )

  // Called by ChatPanel when the user picks a form to fill. Deliberately a
  // separate call from the original analyzeChart, not a reuse of its
  // result — matches the backend's /fill-form being its own endpoint/graph
  // (a chart may get analyzed with no form involved at all), and each form
  // needs a different derived figure from the same chart, so this re-runs
  // extraction with a task scoped to exactly what the chosen form needs
  // rather than assuming the original analysis happened to compute it.
  const handleFillForm = useCallback(
    async (formType) => {
      if (!capturedImage) {
        throw new Error("Analyze a chart first — there's nothing to fill a form from yet.")
      }

      const taskDef = FORM_TASKS[formType]
      let task = { type: taskDef.type, instruction: taskDef.instruction }

      // stock_basis needs a specific date (the date of death) to look up —
      // that's not something the chart alone can tell us, and it's also
      // the form's own manual "Date of Death" field, so ask for it once
      // here rather than guessing a date from the chart.
      if (formType === 'stock_basis') {
        const target = window.prompt(
          'What date of death should the high/low price be looked up for? (e.g. 2019-06-15)'
        )
        if (!target) {
          throw new Error('A date of death is needed to compute the stock basis.')
        }
        task = { ...task, target }
      }

      const result = await analyzeChart(capturedImage, task)
      if (!result.computedAnswer) {
        throw new Error("Couldn't compute the figure this form needs from this chart.")
      }
      setComputedAnswer(result.computedAnswer)

      return fillForm({
        formType,
        computedAnswer: result.computedAnswer,
        image: capturedImage,
        extractedSeries: result.structuredData,
      })
    },
    [capturedImage]
  )

  const handleReset = () => {
    setDescription('')
    setShortDescription('')
    setStructuredData(null)
    setComputedAnswer(null)
    setCapturedImage(null)
  }

  return (
    <div className="app">
      <header className="app-header">
        <div className="app-header-inner">
          <span className="app-icon" aria-hidden="true">📊</span>
          <div>
            <span className="app-eyebrow">Camera · Voice · Chat</span>
            <h1>Diagram Reader</h1>
            <p className="app-subtitle">
              Point your camera at a chart or diagram to hear a description
              and ask questions about it.
            </p>
          </div>
        </div>
      </header>

      <main className="layout">
        <section className="panel chart-panel" aria-label="Chart capture">
          <CameraCapture
            onCapture={handleCapture}
            onDemo={handleDemo}
            isAnalyzing={isAnalyzing}
            capturedImage={capturedImage}
            hasResult={!!structuredData}
            onReset={handleReset}
            speak={speak}
          />
        </section>

        <section className="panel chat-panel-section" aria-label="Chat">
          <ChatPanel
            description={description}
            shortDescription={shortDescription}
            hasChart={!!structuredData}
            confidence={structuredData?.confidence}
            uncertainValues={structuredData?.uncertainValues}
            onAskQuestion={handleAskQuestion}
            onFillForm={handleFillForm}
            isListening={isListening}
            transcript={transcript}
            startListening={startListening}
            stopListening={stopListening}
            isSpeaking={isSpeaking}
            speak={speak}
            stopSpeaking={stopSpeaking}
            rate={rate}
            setRate={setRate}
          />
        </section>
      </main>
    </div>
  )
}
