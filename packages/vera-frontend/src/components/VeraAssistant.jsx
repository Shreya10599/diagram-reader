import { useState, useRef, useCallback, useEffect } from 'react'
import VeraLogo from './VeraLogo.jsx'
import { analyzeSource, askQuestion, fillFormFromChart } from '../mockApi.js'

const STEP = {
  OPENED: 'opened',
  PROGRESS: 'progress',
  MORE: 'more',
  READY: 'ready',
  ASK: 'ask',
  DONE: 'done',
}

const ASK_OPTIONS = [
  "What's the highest value?",
  "What's the lowest value?",
  'What does this mean for me?',
]

const FIELD_LABELS = {
  name: 'Name',
  address: 'Address',
  min: 'Minimum value',
  max: 'Maximum value',
  average: 'Average',
}

function describeFilledFields(fieldsObj) {
  const labels = Object.keys(fieldsObj)
    .filter((key) => fieldsObj[key])
    .map((key) => FIELD_LABELS[key] ?? key)
  if (labels.length <= 1) return labels[0] ?? 'nothing new'
  if (labels.length === 2) return `${labels[0]} and ${labels[1]}`
  return `${labels.slice(0, -1).join(', ')}, and ${labels[labels.length - 1]}`
}

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
 * Two flows live here, matching App.jsx's two-panel layout — but they're
 * not fully independent once both a chart and a form are on hand:
 *  - Chart uploads (picture/photo/link) -> runAnalysis -> POST
 *    /vera/analyze -> onFilled merges the result into the web form
 *    (name/address/min/max/average) on the left. Charts can be added one
 *    after another (STEP.MORE, or "Add another chart" from STEP.READY).
 *  - "Upload form" -> handlePdfFileChange -> onPdfUploaded hands the raw
 *    file to PdfPreview on the right.
 *  - The part that connects them: applyChartsToForm. Every chart that's
 *    been successfully read this session is remembered (chartSourcesRef);
 *    the moment BOTH a chart and a form exist — whichever arrives second
 *    triggers it — each not-yet-applied chart gets sent through POST
 *    /liheap/fill-form-from-chart against the CURRENT state of the form
 *    (not the original upload), and the result replaces `pdfUrl`. So
 *    uploading a second chart after the form's already been filled once
 *    iterates on top of that same document instead of starting over —
 *    the backend just reads whatever PDF it's handed and fills in the
 *    next blank row it finds, income or expenses, decided per chart.
 */
export default function VeraAssistant({
  isOpen,
  onOpenChange,
  onShowAbout,
  fields,
  onFilled,
  pdfUrl,
  onPdfUploaded,
  onRestartForm,
  speak,
}) {
  const [step, setStep] = useState(STEP.OPENED)
  const [messages, setMessages] = useState([{ id: 'm0', text: GREETING }])
  const [progressPct, setProgressPct] = useState(0)
  const [progressTitle, setProgressTitle] = useState('Reading your chart…')
  const [summary, setSummary] = useState('')
  const [isCameraOn, setIsCameraOn] = useState(false)
  const [openFaq, setOpenFaq] = useState(null)
  const [transcriptCopied, setTranscriptCopied] = useState(false)

  const idRef = useRef(1)
  const videoRef = useRef(null)
  const canvasRef = useRef(null)
  const streamRef = useRef(null)
  const fileInputRef = useRef(null)
  const pdfInputRef = useRef(null)
  // Every chart source successfully read this session, in order — kept
  // in a ref (not state) since nothing renders off the list itself, only
  // off what's been done with it. appliedChartCountRef tracks how many
  // have already been folded into the current pdfUrl; applyChartsToForm
  // works through whatever's left each time it runs.
  const chartSourcesRef = useRef([])
  const appliedChartCountRef = useRef(0)

  const addMessage = useCallback(
    (text) => {
      const id = `m${idRef.current++}`
      setMessages((prev) => [...prev, { id, text }])
      speak?.(text)
    },
    [speak]
  )

  const addImagePreview = useCallback((src) => {
    const id = `m${idRef.current++}`
    setMessages((prev) => [...prev, { id, image: src }])
  }, [])

  const stopCamera = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null
    setIsCameraOn(false)
  }, [])

  // Unmount safety net — don't leave the camera running if the panel
  // closes some other way.
  useEffect(() => () => stopCamera(), [stopCamera])

  // The connective piece: works through every chart in chartSourcesRef
  // that hasn't been applied to the current form yet, one at a time,
  // chaining each POST /liheap/fill-form-from-chart's returned PDF into
  // the next call's `formFile` so the document keeps accumulating instead
  // of resetting. Called both when a form is uploaded while charts are
  // already on hand, and when a new chart comes in while a form already
  // exists.
  const applyChartsToForm = useCallback(
    async (startingPdfUrl) => {
      if (appliedChartCountRef.current >= chartSourcesRef.current.length) return
      setStep(STEP.PROGRESS)
      setProgressTitle('Filling in your form…')
      addMessage("Reading your chart(s) and filling in the form — I'll update you live.")
      setProgressPct(8)

      const tick = setInterval(() => {
        setProgressPct((p) => Math.min(p + 6, 92))
      }, 140)

      let workingPdfUrl = startingPdfUrl
      let anySucceeded = false

      while (appliedChartCountRef.current < chartSourcesRef.current.length) {
        const chart = chartSourcesRef.current[appliedChartCountRef.current]
        try {
          const result = await fillFormFromChart({ chartImage: chart, formFile: workingPdfUrl })
          workingPdfUrl = `data:application/pdf;base64,${result.pdfBase64}`
          onPdfUploaded(workingPdfUrl)
          addMessage(result.summary)
          anySucceeded = true
        } catch (err) {
          console.error('Form fill failed for one chart:', err)
          addMessage(
            err.message || "One of your charts couldn't be matched to this form — leaving it out."
          )
        }
        appliedChartCountRef.current += 1
      }

      clearInterval(tick)
      setProgressPct(100)
      setStep(STEP.READY)
      if (anySucceeded) addMessage('Your form preview is updated. What would you like to do now?')
    },
    [addMessage, onPdfUploaded]
  )

  const runAnalysis = useCallback(
    async (source) => {
      addImagePreview(source)
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
        onFilled(result.fields)
        chartSourcesRef.current.push(source)
        addMessage(`I filled in: ${describeFilledFields(result.fields)}.`)

        if (pdfUrl) {
          // A form's already on hand — fold this chart straight into it
          // instead of just parking it for later.
          await applyChartsToForm(pdfUrl)
        } else {
          setStep(STEP.MORE)
          addMessage('Want to add another chart for more fields?')
        }
      } catch (err) {
        clearInterval(tick)
        console.error('Analysis failed:', err)
        addMessage("Something went wrong reading that — want to try again?")
        setStep(STEP.OPENED)
      }
    },
    [addMessage, addImagePreview, onFilled, pdfUrl, applyChartsToForm]
  )

  const handleAddPicture = () => fileInputRef.current?.click()

  const handleFileChange = (event) => {
    const file = event.target.files?.[0]
    event.target.value = '' // allow re-selecting the same file later
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => runAnalysis(reader.result)
    reader.readAsDataURL(file)
  }

  const handleUploadForm = () => pdfInputRef.current?.click()

  // Uploading a form always means "apply everything I've read so far to
  // THIS document" — so re-uploading (a fresh copy, or an entirely
  // different form) resets which charts count as already-applied, and
  // every chart read this session gets tried against it again from
  // scratch.
  const handlePdfFileChange = (event) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => {
      const dataUrl = reader.result
      appliedChartCountRef.current = 0
      onPdfUploaded(dataUrl)
      if (chartSourcesRef.current.length > 0) {
        applyChartsToForm(dataUrl)
      } else {
        addMessage('Your form is loaded — you can see it in the preview on the right.')
      }
    }
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

  // "I'm done" — nothing left to fill, just move to the save screen.
  const handleFinish = () => {
    setStep(STEP.DONE)
    addMessage('All done! Your form is ready.')
  }

  // Downloads the actual uploaded PDF (not the web form) — that's the
  // real document; the min/max/average fields are just VERA's reading
  // of a chart, not something with its own file to save.
  const handleSaveForm = () => {
    if (!pdfUrl) {
      addMessage("There's no uploaded form to save yet — ask me to \"Upload a form\" first.")
      return
    }
    const link = document.createElement('a')
    link.href = pdfUrl
    link.download = 'filled-form.pdf'
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
  }

  const handleBack = () => setStep(STEP.READY)

  const handleRestart = () => {
    stopCamera()
    setStep(STEP.OPENED)
    setMessages([{ id: 'm0', text: GREETING }])
    setProgressPct(0)
    setProgressTitle('Reading your chart…')
    setSummary('')
    setOpenFaq(null)
    idRef.current = 1
    chartSourcesRef.current = []
    appliedChartCountRef.current = 0
    onRestartForm()
  }

  const handleClose = () => {
    stopCamera()
    onOpenChange(false)
  }

  // Reads the most recent VERA message aloud again — handy if someone
  // missed it, or just wants it read rather than reading it themselves.
  const handleReadAloud = () => {
    const last = [...messages].reverse().find((m) => m.text)
    if (last) speak?.(last.text)
  }

  const handleCopyTranscript = async () => {
    const text = messages
      .filter((m) => m.text)
      .map((m) => `VERA: ${m.text}`)
      .join('\n')
    try {
      await navigator.clipboard.writeText(text)
      setTranscriptCopied(true)
      speak?.('Transcript copied')
      setTimeout(() => setTranscriptCopied(false), 2000)
    } catch (err) {
      console.error('Copy failed:', err)
      addMessage("I couldn't copy the transcript — try again.")
    }
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
        {messages.map((m) =>
          m.image ? (
            <img
              key={m.id}
              src={m.image}
              alt="Chart you added"
              className="chat-preview-img"
              onError={(e) => {
                e.currentTarget.style.display = 'none'
              }}
            />
          ) : (
            <div key={m.id} className="bubble">
              <span className="bubble-label">VERA</span>
              {m.text}
            </div>
          )
        )}

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
            <button className="option-btn" onClick={handleUploadForm}>
              <span className="option-icon">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M9 3h6l1 3H8l1-3Z" />
                  <rect x="5" y="6" width="14" height="15" rx="2" />
                </svg>
              </span>
              Upload a form
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

        {step === STEP.MORE && (
          <div className="option-list">
            <button className="option-btn" onClick={() => setStep(STEP.OPENED)}>
              <span className="option-icon">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 5v14M5 12h14" />
                </svg>
              </span>
              Yes, add another chart
              <svg className="chev" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M9 6l6 6-6 6" />
              </svg>
            </button>
            <button
              className="option-btn"
              onClick={() => {
                setStep(STEP.READY)
                addMessage('Done! What would you like to do now?')
              }}
            >
              <span className="option-icon">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M20 6 9 17l-5-5" />
                </svg>
              </span>
              No, I'm done
              <svg className="chev" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M9 6l6 6-6 6" />
              </svg>
            </button>
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
            <button className="option-btn" onClick={() => setStep(STEP.OPENED)}>
              <span className="option-icon">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 5v14M5 12h14" />
                </svg>
              </span>
              Add another chart
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
            <button className="btn btn-primary btn-full" onClick={handleSaveForm}>
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 3v12" />
                <path d="m7 10 5 5 5-5" />
                <path d="M5 21h14" />
              </svg>
              Save my form
            </button>
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

        {/* Always mounted regardless of step — "Add a chart picture" (only
            shown in STEP.OPENED) and "Upload form" (shown in both
            STEP.OPENED and STEP.READY) both need their file picker
            available whenever their button is showing, so these can't
            live inside a step-conditional block. */}
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          onChange={handleFileChange}
          className="visually-hidden"
          aria-label="Upload a picture of a chart"
        />
        <input
          ref={pdfInputRef}
          type="file"
          accept="application/pdf"
          onChange={handlePdfFileChange}
          className="visually-hidden"
          aria-label="Upload a form as a PDF"
        />

        {/* Hidden canvas used only to grab a still frame from the live camera. */}
        <canvas ref={canvasRef} style={{ display: 'none' }} />
      </div>

      <div className="vera-footer">
        <button className="vera-footer-btn" onClick={handleReadAloud} aria-label="Read the last message aloud">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M11 5 6 9H3v6h3l5 4V5Z" />
            <path d="M15.5 8.5a5 5 0 0 1 0 7" />
          </svg>
          Read aloud
        </button>
        <button className="vera-footer-btn" onClick={handleCopyTranscript} aria-label="Copy the conversation transcript">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <rect x="8" y="3" width="10" height="14" rx="2" />
            <path d="M6 7H5a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2v-1" />
          </svg>
          {transcriptCopied ? 'Copied!' : 'Copy transcript'}
        </button>
      </div>
    </div>
  )
}
