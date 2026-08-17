import { useState, useRef, useCallback, useEffect } from 'react'
import VeraLogo from './VeraLogo.jsx'
import { analyzeSource, askQuestion, fillFormFromChart } from '../mockApi.js'

const STEP = {
  OPENED: 'opened',
  PROGRESS: 'progress',
  READY: 'ready',
  ASK: 'ask',
  DONE: 'done',
}

const ASK_OPTIONS = [
  "What's the highest value?",
  "What's the lowest value?",
  'What does this mean for me?',
]

const FAQ = [
  {
    q: 'What file types can I upload?',
    a: 'Any common photo format works — JPG, PNG, or HEIC from your phone.',
  },
  {
    q: 'Is my information saved?',
    a: 'Only on this device, until you tap "Save my form."',
  },
  {
    q: 'Can I edit the filled form?',
    a: 'Yes — every field on the form stays editable after VERA fills it in.',
  },
]

const GREETING = "Hi, I'm VERA. How can I help you today?"

/**
 * VeraAssistant — the whole floating chat widget: closed it's just the
 * round button (`.fab`), open it's the docked panel beside the form.
 * Every turn is a button tap, on purpose — no typing or voice input
 * anywhere, since this is built for an audience that shouldn't have to
 * type to use it. See mockApi.js for the shape the real backend needs
 * to match.
 *
 * Two things happen here, always in this order:
 *  1. runAnalysis: the person adds ONE chart (picture/photo/link) ->
 *     POST /vera/analyze -> fills the fixed name/address/min/max/average
 *     form on the landing page.
 *  2. runFillFormFromChart: once a chart's been read, "Upload form" in
 *     STEP.READY lets them add the real LIHEAP PDF -> POST
 *     /liheap/fill-form-from-chart, which decides whether that SAME chart
 *     is an income chart (member x last/this/next month) or an expenses
 *     chart (category x amount), then hands back an actual filled copy of
 *     the right table — page 3 or page 5 — ready to download as a real
 *     PDF, not another on-screen worksheet.
 */
export default function VeraAssistant({ isOpen, onOpenChange, onShowAbout, fields, onFilled, onRestartForm, speak }) {
  const [step, setStep] = useState(STEP.OPENED)
  const [messages, setMessages] = useState([{ id: 'm0', text: GREETING }])
  const [progressPct, setProgressPct] = useState(0)
  const [progressTitle, setProgressTitle] = useState('Reading your chart…')
  const [summary, setSummary] = useState('')
  const [isCameraOn, setIsCameraOn] = useState(false)
  const [openFaq, setOpenFaq] = useState(null)
  // The last chart source successfully read (data URL, or link text for
  // the still-mocked link path) — "Upload form" in STEP.READY only ever
  // appears after this is set, so runFillFormFromChart can always reuse
  // it instead of asking the person to upload the same chart twice.
  const [chartSource, setChartSource] = useState(null)
  // Base64 of the real filled PDF once runFillFormFromChart succeeds —
  // presence of this (rather than a generic "mode") is what switches
  // STEP.DONE's primary button from "Save my form" (print) to
  // "Download filled PDF".
  const [filledPdfBase64, setFilledPdfBase64] = useState(null)

  const idRef = useRef(1)
  const videoRef = useRef(null)
  const canvasRef = useRef(null)
  const streamRef = useRef(null)
  const fileInputRef = useRef(null)
  const formFileInputRef = useRef(null)

  const addMessage = useCallback(
    (text) => {
      const id = `m${idRef.current++}`
      setMessages((prev) => [...prev, { id, text }])
      speak?.(text)
    },
    [speak]
  )

  const stopCamera = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null
    setIsCameraOn(false)
  }, [])

  // Unmount safety net — don't leave the camera running if the panel
  // closes some other way.
  useEffect(() => () => stopCamera(), [stopCamera])

  const runAnalysis = useCallback(
    async (source) => {
      setStep(STEP.PROGRESS)
      setProgressTitle('Reading your chart…')
      addMessage("Working on it — I'll update you live.")
      setProgressPct(8)

      // Simulated live progress while the real request is in flight —
      // the real backend would push actual percentages over the same seam.
      const tick = setInterval(() => {
        setProgressPct((p) => Math.min(p + 7, 92))
      }, 140)

      try {
        const result = await analyzeSource(source)
        clearInterval(tick)
        setProgressPct(100)
        setSummary(result.summary)
        setChartSource(source)
        onFilled(result.fields)
        setStep(STEP.READY)
        addMessage('Done! What would you like to do now?')
      } catch (err) {
        clearInterval(tick)
        console.error('Analysis failed:', err)
        addMessage("Something went wrong reading that — want to try again?")
        setStep(STEP.OPENED)
      }
    },
    [addMessage, onFilled]
  )

  // Reads the uploaded LIHEAP form and fills whichever table the chart
  // actually turns out to be — income (page 3) or expenses (page 5),
  // decided server-side. `chartSource` is guaranteed set here — "Upload
  // form" only shows up in STEP.READY, which only exists after
  // runAnalysis has succeeded — so there's no "add a chart" detour: one
  // form upload, one real filled PDF back.
  const runFillFormFromChart = useCallback(
    async (formFileDataUrl) => {
      if (!chartSource) {
        addMessage('Add a chart first — then upload the form and I can fill it in.')
        return
      }
      setStep(STEP.PROGRESS)
      setProgressTitle('Figuring out what your chart shows…')
      addMessage("Reading your chart and form to figure out which table to fill — I'll update you live.")
      setProgressPct(8)

      const tick = setInterval(() => {
        setProgressPct((p) => Math.min(p + 6, 92))
      }, 140)

      try {
        const result = await fillFormFromChart({ chartImage: chartSource, formFile: formFileDataUrl })
        clearInterval(tick)
        setProgressPct(100)
        setFilledPdfBase64(result.pdfBase64)
        setSummary(result.summary)
        addMessage(result.summary)
        setStep(STEP.READY)
      } catch (err) {
        clearInterval(tick)
        console.error('Form fill failed:', err)
        addMessage(err.message || 'Something went wrong filling that form — want to try again?')
        setStep(STEP.READY)
      }
    },
    [addMessage, chartSource]
  )

  const handleAddPicture = () => fileInputRef.current?.click()
  const handleUploadForm = () => formFileInputRef.current?.click()

  const handleFileChange = (event) => {
    const file = event.target.files?.[0]
    event.target.value = '' // allow re-selecting the same file later
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => runAnalysis(reader.result)
    reader.readAsDataURL(file)
  }

  // Separate input — always the FORM itself (PDF or a photo of one), never
  // a chart.
  const handleFormFileChange = (event) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => runFillFormFromChart(reader.result)
    reader.readAsDataURL(file)
  }

  const handleTakePhoto = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'environment' },
        audio: false,
      })
      streamRef.current = stream
      if (videoRef.current) videoRef.current.srcObject = stream
      setIsCameraOn(true)
    } catch (err) {
      console.error('Camera access failed:', err)
      addMessage('I couldn’t open your camera — check permissions, or try "Add a chart picture" instead.')
    }
  }

  const handleCapturePhoto = () => {
    const video = videoRef.current
    const canvas = canvasRef.current
    if (!video || !canvas) return
    canvas.width = video.videoWidth
    canvas.height = video.videoHeight
    canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height)
    const imageDataUrl = canvas.toDataURL('image/jpeg', 0.9)
    stopCamera()
    runAnalysis(imageDataUrl)
  }

  const handleAddLink = async () => {
    try {
      const text = await navigator.clipboard.readText()
      if (!text) throw new Error('Clipboard empty')
      runAnalysis(text)
    } catch (err) {
      addMessage("I couldn't read a link from your clipboard — copy a link first, then try again.")
    }
  }

  const handleTellMeAboutIt = () => addMessage(summary)

  const handleAskQuestion = () => {
    setStep(STEP.ASK)
    addMessage('Sure — what do you want to know?')
  }

  const handleAskOption = async (question) => {
    const answer = await askQuestion(question, fields)
    addMessage(answer)
  }

  // "I'm done" in STEP.READY — nothing left to fill, just move to the
  // save/download screen.
  const handleFinish = () => {
    setStep(STEP.DONE)
    addMessage('All done! Your form is ready.')
  }

  // The actual "how do I access the form" answer: a real filled PDF,
  // downloaded client-side from the base64 runFillFormFromChart got back
  // — no extra backend round trip needed.
  const handleDownloadFilledPdf = () => {
    if (!filledPdfBase64) return
    const byteChars = atob(filledPdfBase64)
    const byteNumbers = new Array(byteChars.length)
    for (let i = 0; i < byteChars.length; i++) byteNumbers[i] = byteChars.charCodeAt(i)
    const blob = new Blob([new Uint8Array(byteNumbers)], { type: 'application/pdf' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'liheap-form-filled.pdf'
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  }

  const handleBack = () => setStep(STEP.READY)

  const handleRestart = () => {
    stopCamera()
    setStep(STEP.OPENED)
    setMessages([{ id: 'm0', text: GREETING }])
    setProgressPct(0)
    setProgressTitle('Reading your chart…')
    setSummary('')
    setChartSource(null)
    setFilledPdfBase64(null)
    setOpenFaq(null)
    idRef.current = 1
    onRestartForm()
  }

  const handleClose = () => {
    stopCamera()
    onOpenChange(false)
  }

  if (!isOpen) {
    return (
      <button className="fab" onClick={() => onOpenChange(true)} aria-label="Open VERA assistant">
        <svg width="28" height="28" viewBox="0 0 24 24" aria-hidden="true">
          <rect x="3" y="4" width="18" height="13" rx="4" fill="#fff" />
          <path d="M7 17 L6 20 L12 17 Z" fill="#fff" />
          <path d="M8 9.3h8M8 12.7h5" stroke="var(--logo-bg)" strokeWidth="1.4" strokeLinecap="round" />
        </svg>
      </button>
    )
  }

  return (
    <div className="vera-panel" role="dialog" aria-label="VERA assistant">
      <div className="vera-panel-head">
        {step === STEP.ASK ? (
          <span className="vera-panel-head-title">
            <button className="icon-btn" onClick={handleBack} aria-label="Back" style={{ width: 26, height: 26 }}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
                <path d="M15 6l-6 6 6 6" />
              </svg>
            </button>
            VERA Assistant
          </span>
        ) : (
          <span className="vera-panel-head-title">
            <VeraLogo size={26} />
            VERA Assistant
          </span>
        )}

        <span className="vera-panel-head-icons">
          {step !== STEP.ASK && (
            <>
              <button className="icon-btn" onClick={handleRestart} aria-label="Restart">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21 12a9 9 0 1 1-3-6.7" />
                  <path d="M21 3v6h-6" />
                </svg>
              </button>
              <button className="icon-btn" onClick={onShowAbout} aria-label="More info about VERA">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
                  <circle cx="12" cy="12" r="9" />
                  <path d="M9.3 9a2.7 2.7 0 0 1 5.2.9c0 1.6-2.6 2.1-2.6 3.6" strokeLinecap="round" />
                  <path d="M12 17h.01" strokeLinecap="round" />
                </svg>
              </button>
            </>
          )}
          <button className="icon-btn" onClick={handleClose} aria-label="Close">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
              <path d="M6 6l12 12M18 6 6 18" />
            </svg>
          </button>
        </span>
      </div>

      <div className="vera-body" role="log" aria-live="polite" aria-label="Conversation with VERA">
        {messages.map((m) => (
          <div key={m.id} className="bubble">
            <span className="bubble-label">VERA</span>
            {m.text}
          </div>
        ))}

        {step === STEP.OPENED && !isCameraOn && (
          <div className="option-list">
            <button className="option-btn" onClick={handleAddPicture}>
              <span className="option-icon">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M4 16l4.5-5 3 3.5L16 9l4 7" />
                  <rect x="3" y="4" width="18" height="16" rx="2" />
                </svg>
              </span>
              Add a chart picture
              <svg className="chev" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M9 6l6 6-6 6" />
              </svg>
            </button>
            <button className="option-btn" onClick={handleTakePhoto}>
              <span className="option-icon">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M4 14V6a2 2 0 0 1 2-2h2l1.5-2h5L16 4h2a2 2 0 0 1 2 2v8" />
                  <circle cx="12" cy="13" r="3.5" />
                </svg>
              </span>
              Take a chart photo
              <svg className="chev" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M9 6l6 6-6 6" />
              </svg>
            </button>
            <button className="option-btn" onClick={handleAddLink}>
              <span className="option-icon">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M10 13a5 5 0 0 0 7.5.4l2-2a5 5 0 0 0-7-7l-1.2 1.1" />
                  <path d="M14 11a5 5 0 0 0-7.5-.4l-2 2a5 5 0 0 0 7 7l1.1-1.1" />
                </svg>
              </span>
              Add a chart link
              <svg className="chev" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M9 6l6 6-6 6" />
              </svg>
            </button>
          </div>
        )}

        {step === STEP.OPENED && isCameraOn && (
          <div className="camera-live">
            <video ref={videoRef} autoPlay playsInline aria-label="Live camera preview" />
            <button className="btn btn-primary btn-full" onClick={handleCapturePhoto}>
              Capture photo
            </button>
            <button className="btn btn-outline btn-full" onClick={stopCamera}>
              Cancel
            </button>
          </div>
        )}

        {step === STEP.PROGRESS && (
          <div className="progress-card">
            <div className="progress-head">
              <div className="option-icon" style={{ background: 'var(--accent-wash)' }}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="11" cy="11" r="7" />
                  <path d="m20 20-3.5-3.5" />
                </svg>
              </div>
              <span className="progress-title">{progressTitle}</span>
            </div>
            <div className="progress-track">
              <div className="progress-fill" style={{ width: `${progressPct}%` }} />
            </div>
            <span className="progress-pct">{progressPct}% completed</span>
          </div>
        )}

        {step === STEP.READY && (
          <div className="option-list">
            <button className="option-btn" onClick={handleTellMeAboutIt}>
              <span className="option-icon">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M4 6h16M4 12h10M4 18h7" />
                </svg>
              </span>
              Tell me about it
              <svg className="chev" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M9 6l6 6-6 6" />
              </svg>
            </button>
            <button className="option-btn" onClick={handleAskQuestion}>
              <span className="option-icon">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.1">
                  <circle cx="12" cy="12" r="9" />
                  <path d="M9.3 9a2.7 2.7 0 0 1 5.2.9c0 1.6-2.6 2.1-2.6 3.6" />
                  <path d="M12 17h.01" />
                </svg>
              </span>
              Ask a question
              <svg className="chev" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M9 6l6 6-6 6" />
              </svg>
            </button>
            <button className="option-btn" onClick={handleUploadForm}>
              <span className="option-icon">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M9 3h6l1 3H8l1-3Z" />
                  <rect x="5" y="6" width="14" height="15" rx="2" />
                </svg>
              </span>
              Upload form
              <svg className="chev" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M9 6l6 6-6 6" />
              </svg>
            </button>
            <button className="option-btn" onClick={handleFinish}>
              <span className="option-icon">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M20 6 9 17l-5-5" />
                </svg>
              </span>
              I'm done
              <svg className="chev" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M9 6l6 6-6 6" />
              </svg>
            </button>
          </div>
        )}

        {step === STEP.ASK && (
          <div className="option-list">
            {ASK_OPTIONS.map((question) => (
              <button key={question} className="option-btn" onClick={() => handleAskOption(question)}>
                {question}
                <svg className="chev" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M9 6l6 6-6 6" />
                </svg>
              </button>
            ))}
          </div>
        )}

        {step === STEP.DONE && (
          <>
            {filledPdfBase64 ? (
              <button className="btn btn-primary btn-full" onClick={handleDownloadFilledPdf}>
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 3v12" />
                  <path d="m7 10 5 5 5-5" />
                  <path d="M5 21h14" />
                </svg>
                Download filled PDF
              </button>
            ) : (
              <button className="btn btn-primary btn-full" onClick={() => window.print()}>
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 3v12" />
                  <path d="m7 10 5 5 5-5" />
                  <path d="M5 21h14" />
                </svg>
                Save my form
              </button>
            )}
            <button className="btn btn-outline btn-full" onClick={handleRestart}>
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 12a9 9 0 1 1-3-6.7" />
                <path d="M21 3v6h-6" />
              </svg>
              Start over
            </button>
            <div className="faq-block">
              <p className="faq-label">Frequently asked questions</p>
              {FAQ.map((item, i) => (
                <div key={item.q}>
                  <button
                    className="faq-row"
                    aria-expanded={openFaq === i}
                    onClick={() => setOpenFaq(openFaq === i ? null : i)}
                  >
                    {item.q}
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <path d="m6 9 6 6 6-6" />
                    </svg>
                  </button>
                  {openFaq === i && <p className="faq-answer">{item.a}</p>}
                </div>
              ))}
            </div>
          </>
        )}

        {/* Always mounted regardless of step — "Add a chart picture" and
            "Upload form" both need their file picker available whenever
            their button is showing. */}
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          onChange={handleFileChange}
          className="visually-hidden"
          aria-label="Upload a picture of a chart"
        />
        <input
          ref={formFileInputRef}
          type="file"
          accept="image/*,application/pdf"
          onChange={handleFormFileChange}
          className="visually-hidden"
          aria-label="Upload a form (PDF or photo)"
        />

        {/* Hidden canvas used only to grab a still frame from the live camera. */}
        <canvas ref={canvasRef} style={{ display: 'none' }} />
      </div>
    </div>
  )
}
