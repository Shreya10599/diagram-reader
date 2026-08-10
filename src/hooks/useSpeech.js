import { useState, useRef, useCallback, useEffect } from 'react'

/**
 * useSpeech — wraps the two Web Speech APIs so components don't have
 * to deal with browser prefixes / setup directly.
 *
 * 1. SpeechRecognition   -> turns mic audio into text (speech-to-text)
 * 2. speechSynthesis     -> turns text into spoken audio (text-to-speech)
 *
 * Browser support note: SpeechRecognition works in Chrome/Edge out of the
 * box. Safari support is spotty. For a hackathon demo, just demo on Chrome
 * and mention the limitation if asked — don't burn time chasing Safari.
 */
export function useSpeech() {
  const [isListening, setIsListening] = useState(false)
  const [transcript, setTranscript] = useState('')
  const [isSpeaking, setIsSpeaking] = useState(false)
  // Speech rate for spoken output (0.75 slow / 1 normal / 1.5 fast). Lives
  // here rather than in a component so it survives across every message
  // spoken during the session, not just the next one.
  const [rate, setRate] = useState(1)
  const recognitionRef = useRef(null)

  // Set up the recognition object once on mount.
  useEffect(() => {
    const SpeechRecognition =
      window.SpeechRecognition || window.webkitSpeechRecognition

    if (!SpeechRecognition) {
      console.warn('SpeechRecognition not supported in this browser.')
      return
    }

    const recognition = new SpeechRecognition()
    recognition.continuous = false // stop automatically after a pause
    recognition.interimResults = false // only give us the final result
    recognition.lang = 'en-US'

    recognition.onresult = (event) => {
      const text = event.results[0][0].transcript
      setTranscript(text)
    }

    recognition.onend = () => {
      setIsListening(false)
    }

    recognition.onerror = (event) => {
      console.error('Speech recognition error:', event.error)
      setIsListening(false)
    }

    recognitionRef.current = recognition

    return () => {
      recognition.stop()
    }
  }, [])

  const startListening = useCallback(() => {
    if (!recognitionRef.current) return
    setTranscript('')
    setIsListening(true)
    recognitionRef.current.start()
  }, [])

  const stopListening = useCallback(() => {
    if (!recognitionRef.current) return
    recognitionRef.current.stop()
    setIsListening(false)
  }, [])

  // speak() reads text aloud. Cancels anything currently playing first,
  // so a new answer doesn't overlap with a previous one.
  const speak = useCallback(
    (text) => {
      if (!window.speechSynthesis) {
        console.warn('speechSynthesis not supported in this browser.')
        return
      }
      window.speechSynthesis.cancel()

      const utterance = new SpeechSynthesisUtterance(text)
      utterance.rate = rate
      utterance.onstart = () => setIsSpeaking(true)
      utterance.onend = () => setIsSpeaking(false)

      window.speechSynthesis.speak(utterance)
    },
    [rate]
  )

  const stopSpeaking = useCallback(() => {
    window.speechSynthesis?.cancel()
    setIsSpeaking(false)
  }, [])

  return {
    isListening,
    transcript,
    startListening,
    stopListening,
    isSpeaking,
    speak,
    stopSpeaking,
    rate,
    setRate,
  }
}
