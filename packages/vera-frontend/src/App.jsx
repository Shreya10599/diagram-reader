import { useState, useCallback, useRef } from 'react'
import LandingForm from './components/LandingForm.jsx'
import PdfPreview from './components/PdfPreview.jsx'
import VeraAssistant from './components/VeraAssistant.jsx'
import AboutVera from './components/AboutVera.jsx'
import { useSpeech } from './hooks/useSpeech.js'

/**
 * App — top-level state for VERA.
 *
 * Two independent upload flows live side by side once the user has
 * started:
 *   - Chart uploads (picture/photo/link) only ever update `fields`
 *     (the web form on the left, via LandingForm).
 *   - A PDF upload only ever updates `pdfUrl` (the preview on the
 *     right, via PdfPreview).
 * Neither path touches the other's state — see VeraAssistant, where
 * they're wired to two completely separate handlers.
 *
 * VeraAssistant is a floating widget on top of all this — closed it's
 * just the round button, open it's the docked chat panel — so
 * opening/closing it never navigates anywhere. AboutVera is the one
 * exception: it's a real full-page overlay, reached via the "?" icon
 * inside the chat, or shown first before the user has clicked "Start
 * filling the form now."
 */
export default function App() {
  const [fields, setFields] = useState(null)
  const [pdfUrl, setPdfUrl] = useState(null)
  const [isChatOpen, setIsChatOpen] = useState(false)
  const [showAbout, setShowAbout] = useState(false)
  const [hasStarted, setHasStarted] = useState(false)

  const { speak } = useSpeech()
  const pdfUrlRef = useRef(null)
  pdfUrlRef.current = pdfUrl

  const handleFieldChange = useCallback((key, value) => {
    setFields((prev) => (prev ? { ...prev, [key]: value } : prev))
  }, [])

  const handleFilled = useCallback((newFields) => {
    setFields((prev) => ({
      ...(prev ?? { name: '', address: '', min: '', max: '', average: '' }),
      ...newFields,
    }))
  }, [])

  const handlePdfUploaded = useCallback((url) => {
    setPdfUrl(url)
  }, [])

  const handleRestartForm = useCallback(() => {
    setFields(null)
    // Release the old preview's memory before clearing it.
    if (pdfUrlRef.current) URL.revokeObjectURL(pdfUrlRef.current)
    setPdfUrl(null)
  }, [])

  return (
    <div className="app-shell">
      {!hasStarted ? (
        <AboutVera onStart={() => setHasStarted(true)} />
      ) : (
        <>
          <header className="landing-header">
            <div className="header-icon-row" aria-hidden="true">
              <div className="report-icon-badge">
                <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="var(--ink-soft)" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="4" y="3" width="16" height="18" rx="2" />
                  <path d="M8 14v3M12 11v6M16 8v9" />
                </svg>
              </div>
              <div className="report-icon-badge report-icon-badge-income">
                <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="var(--done)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M3 17l6-6 4 4 8-8" />
                  <path d="M17 7h4v4" />
                </svg>
              </div>
              <div className="report-icon-badge">
                <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M3 7l6 6 4-4 8 8" />
                  <path d="M17 17h4v-4" />
                </svg>
              </div>
            </div>
          </header>

          <div className="workspace">
            <LandingForm fields={fields} onFieldChange={handleFieldChange} />
            <PdfPreview pdfUrl={pdfUrl} />
          </div>

          <VeraAssistant
            isOpen={isChatOpen}
            onOpenChange={setIsChatOpen}
            onShowAbout={() => setShowAbout(true)}
            fields={fields}
            onFilled={handleFilled}
            onPdfUploaded={handlePdfUploaded}
            onRestartForm={handleRestartForm}
            speak={speak}
          />
          {showAbout && <AboutVera onClose={() => setShowAbout(false)} />}
        </>
      )}
    </div>
  )
}
