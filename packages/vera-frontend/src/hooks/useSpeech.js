import { useState, useCallback } from 'react'

/**
 * useSpeech — wraps speechSynthesis so VERA's messages can be read
 * aloud automatically. This app has no typing or voice-input UI
 * anywhere (every turn is a button tap, by design, for an audience
 * that shouldn't have to type) — so this hook is output-only. If a
 * future version adds voice input back, that's where
 * SpeechRecognition would go; don't bring it back just to leave it
 * unused.
 *
 * Browser support note: speechSynthesis works in Chrome/Edge out of
 * the box. Safari support is spotty — demo on Chrome.
 */
export function useSpeech() {
  const [isSpeaking, setIsSpeaking] = useState(false)

  // speak() reads text aloud. Cancels anything currently playing first,
  // so a new message doesn't overlap with a previous one.
  const speak = useCallback((text) => {
    if (!window.speechSynthesis) {
      console.warn('speechSynthesis not supported in this browser.')
      return
    }
    window.speechSynthesis.cancel()

    const utterance = new SpeechSynthesisUtterance(text)
    utterance.onstart = () => setIsSpeaking(true)
    utterance.onend = () => setIsSpeaking(false)

    window.speechSynthesis.speak(utterance)
  }, [])

  const stopSpeaking = useCallback(() => {
    window.speechSynthesis?.cancel()
    setIsSpeaking(false)
  }, [])

  return {
    isSpeaking,
    speak,
    stopSpeaking,
  }
}
