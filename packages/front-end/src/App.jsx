import { useState, useCallback } from 'react'
import CameraCapture from './components/CameraCapture.jsx'
import ChatPanel from './components/ChatPanel.jsx'
import { useSpeech } from './hooks/useSpeech.js'
import { analyzeChart, analyzeDemoChart, askQuestion } from './mockApi.js'

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
  const [capturedImage, setCapturedImage] = useState(null) // data URL, or 'DEMO_PIE'
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
      } catch (err) {
        console.error('Analysis failed:', err)
        const errorMsg = 'Something went wrong analyzing the image. Try again.'
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

  const handleDemo = useCallback(
    () => runAnalysis(() => analyzeDemoChart(), 'DEMO_PIE'),
    [runAnalysis]
  )

  const handleAskQuestion = useCallback(
    async (question) => askQuestion(question, structuredData),
    [structuredData]
  )

  const handleReset = () => {
    setDescription('')
    setShortDescription('')
    setStructuredData(null)
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
            onAskQuestion={handleAskQuestion}
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
