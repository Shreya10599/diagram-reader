import { useState, useCallback } from 'react'
import LandingForm from './components/LandingForm.jsx'
import PdfPreview from './components/PdfPreview.jsx'
import VeraAssistant from './components/VeraAssistant.jsx'
import AboutVera from './components/AboutVera.jsx'
import { useSpeech } from './hooks/useSpeech.js'

/**
 * App — top-level state for VERA.
 *
 * Two panels live side by side once the user has started:
 *   - `fields` (the web form on the left, via LandingForm) — filled by
 *     every chart upload, via POST /vera/analyze.
 *   - `pdfUrl` (the preview on the right, via PdfPreview) — the current
 *     state of whatever form the person uploaded. It starts as the raw
 *     file they picked, but once a chart's been read AND a form exists,
 *     VeraAssistant folds that chart's data straight into it via POST
 *     /liheap/fill-form-from-chart and replaces `pdfUrl` with the result
 *     — so it's not a static preview once both a chart and a form are on
 *     hand, it's the live, iteratively-filled document. `pdfUrl` holds a
 *     data: URL (not a blob: URL) specifically so it can be handed
 *     straight back to that endpoint as the next call's `formFile`
 *     without re-reading anything from disk.
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

  const handleFieldChange = useCallback((key, value) => {
    setFields((prev) => (prev ? { ...prev, [key]: value } : prev))
  }, [])

  const handleFilled = useCallback((newFields) => {
    setFields((prev) => ({
      ...(prev ?? { name: '', address: '', min: '', max: '', average: '' }),
      ...newFields,
    }))
  }, [])

  // Also used mid-session to swap in the result of a chart just having
  // been folded into the form — see VeraAssistant.jsx's applyChartsToForm.
  const handlePdfUploaded = useCallback((dataUrl) => {
    setPdfUrl(dataUrl)
  }, [])

  const handleRestartForm = useCallback(() => {
    setFields(null)
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
            pdfUrl={pdfUrl}
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
