import { useState, useCallback } from 'react'
import LandingForm from './components/LandingForm.jsx'
import VeraAssistant from './components/VeraAssistant.jsx'
import AboutVera from './components/AboutVera.jsx'
import { useSpeech } from './hooks/useSpeech.js'

/**
 * App — top-level state for VERA.
 *
 * The landing page (VeraLogo + form) is always on screen. VeraAssistant
 * is a floating widget on top of it — closed it's just the round
 * button, open it's the docked chat panel — so opening/closing it never
 * navigates anywhere. AboutVera is the one exception: it's a real
 * full-page overlay, reached via the "?" icon inside the chat.
 *
 * `fields` is null until VERA has actually read something — that's
 * what keeps the landing form disabled/empty on first load and lets
 * LandingForm switch Minimum/Maximum/Average to the green "AI-filled"
 * style once it's not.
 */
export default function App() {
  const [fields, setFields] = useState(null)
  const [isChatOpen, setIsChatOpen] = useState(false)
  const [showAbout, setShowAbout] = useState(false)
  const [hasStarted, setHasStarted] = useState(false)

  const { speak } = useSpeech()

  const handleFieldChange = useCallback((key, value) => {
    setFields((prev) => (prev ? { ...prev, [key]: value } : prev))
  }, [])

  const handleFilled = useCallback((newFields) => {
    setFields(newFields)
  }, [])

  const handleRestartForm = useCallback(() => {
    setFields(null)
  }, [])

  return (
  <div className="app-shell">
    {!hasStarted ? (
      <AboutVera onStart={() => setHasStarted(true)} />
    ) : (
      <>
        <LandingForm fields={fields} onFieldChange={handleFieldChange} />
        <VeraAssistant
          isOpen={isChatOpen}
          onOpenChange={setIsChatOpen}
          onShowAbout={() => setShowAbout(true)}
          fields={fields}
          onFilled={handleFilled}
          onRestartForm={handleRestartForm}
          speak={speak}
        />
        {showAbout && <AboutVera onClose={() => setShowAbout(false)} />}
      </>
    )}
  </div>
)
  
}
