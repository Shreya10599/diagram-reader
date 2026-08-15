import { useState, useEffect, useRef } from 'react'

const WELCOME_MESSAGE =
  "Hi! I'm ready whenever you are — capture a chart, upload a photo, or try the sample chart, and I'll describe it. You can also just type a question below."

const SUGGESTED_QUESTION = 'What does this chart demonstrate?'

// Backs the "Slow / Normal / Fast" control — values feed straight into
// SpeechSynthesisUtterance.rate inside useSpeech.js.
const RATE_OPTIONS = [
  { label: 'Slow', value: 0.75 },
  { label: 'Normal', value: 1 },
  { label: 'Fast', value: 1.5 },
]

/**
 * ChatPanel — the conversation view. Always visible (not gated behind a
 * captured chart) so it's a stable seam for a backend agent later. Lets
 * the student ask follow-up questions (typed or spoken), and reads
 * answers aloud automatically since the user may not be looking at the
 * screen at all.
 *
 * All speech state (speak/stopSpeaking/isSpeaking/rate/…) is owned by
 * App.jsx's single useSpeech() call and passed down as props, so "Stop
 * speaking" here can interrupt an announcement fired from the capture
 * flow too, not just chat messages.
 *
 * Rather than dumping the full chart description straight into the chat,
 * a new chart shows up as a clickable "suggested question" — the answer
 * (the description) only appears once the student actually asks it. This
 * mirrors how a real Q&A turn with a backend agent will work later.
 *
 * Accessibility notes (don't skip these, they're most of the point):
 * - aria-live="polite" on the message list means a screen reader will
 *   announce new messages automatically, without the user needing to
 *   navigate to find them.
 * - Every interactive control has a real label, not just a visual icon.
 * - The mic button's state (listening / not) is announced via aria-pressed.
 * - "Stop speaking" stays visible and focusable at all times (just
 *   disabled when nothing's playing) so it's always where a keyboard
 *   user expects it, not popping in and out of the tab order.
 */
export default function ChatPanel({
  description,
  shortDescription,
  hasChart,
  confidence,
  uncertainValues,
  onAskQuestion,
  isListening,
  transcript,
  startListening,
  stopListening,
  isSpeaking,
  speak,
  stopSpeaking,
  rate,
  setRate,
}) {
  const [messages, setMessages] = useState([
    { id: 'welcome', role: 'assistant', text: WELCOME_MESSAGE },
  ])
  const [inputText, setInputText] = useState('')
  const [isWaitingForAnswer, setIsWaitingForAnswer] = useState(false)
  const [depth, setDepth] = useState('full') // 'full' | 'quick'
  const [copyStatus, setCopyStatus] = useState('idle') // 'idle' | 'copied'
  const messagesEndRef = useRef(null)
  const lastDescriptionRef = useRef(null)
  const idRef = useRef(0)
  // Tracks the most recently *revealed* chart description message, so the
  // depth toggle can update it live without hunting through the array.
  const lastRevealedRef = useRef(null)

  const nextId = () => {
    idRef.current += 1
    return `msg-${idRef.current}`
  }

  // Drop a suggested question into the chat whenever a new chart comes
  // in (rather than replacing the conversation) — the chat stays alive
  // across multiple charts. Both description lengths are carried on the
  // message so the depth toggle can pick either one later.
  useEffect(() => {
    if (description && description !== lastDescriptionRef.current) {
      lastDescriptionRef.current = description
      setMessages((prev) => [
        ...prev,
        {
          id: nextId(),
          role: 'suggestion',
          prompt: SUGGESTED_QUESTION,
          fullAnswer: description,
          shortAnswer: shortDescription,
        },
      ])

      // Confidence is safety-relevant — a blind/low-vision user can't just
      // glance at the chart to spot-check a number themselves — so this is
      // surfaced right away, not gated behind tapping the suggested
      // question the way the description itself is.
      if (confidence && confidence !== 'high') {
        const caveat =
          uncertainValues && uncertainValues.length > 0
            ? `Heads up — I'm not fully confident about some values in this chart: ${uncertainValues.join(', ')}. You may want to double check those against the original.`
            : "Heads up — I'm not fully confident about some of the values in this chart. You may want to double check against the original."
        setMessages((prev) => [
          ...prev,
          { id: nextId(), role: 'assistant', text: caveat, isCaveat: true },
        ])
        speak(caveat)
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [description, shortDescription, confidence, uncertainValues])

  // When speech-to-text finishes, drop the transcript into the input
  // box automatically so the student can see it before it sends.
  useEffect(() => {
    if (transcript) {
      setInputText(transcript)
    }
  }, [transcript])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const handleSend = async () => {
    const question = inputText.trim()
    if (!question) return

    setMessages((prev) => [...prev, { id: nextId(), role: 'user', text: question }])
    setInputText('')
    setIsWaitingForAnswer(true)

    try {
      const answer = await onAskQuestion(question)
      setMessages((prev) => [...prev, { id: nextId(), role: 'assistant', text: answer }])
      speak(answer)
    } catch (err) {
      const errorMsg = 'Sorry, something went wrong answering that. Try again.'
      setMessages((prev) => [...prev, { id: nextId(), role: 'assistant', text: errorMsg }])
      speak(errorMsg)
    } finally {
      setIsWaitingForAnswer(false)
    }
  }

  // Clicking a suggested question turns it into a real Q&A turn: the
  // chip becomes the "You" message, then the answer that was already
  // sitting behind it gets revealed (with a brief "Thinking…" beat so
  // it doesn't feel like it was there all along) and read aloud, using
  // whichever depth (quick/full) is currently selected.
  const handleSuggestionClick = async (suggestion) => {
    setMessages((prev) =>
      prev.map((m) =>
        m.id === suggestion.id ? { id: m.id, role: 'user', text: suggestion.prompt } : m
      )
    )
    setIsWaitingForAnswer(true)
    await new Promise((resolve) => setTimeout(resolve, 500))

    const answerText = depth === 'quick' ? suggestion.shortAnswer : suggestion.fullAnswer
    const assistantId = nextId()
    lastRevealedRef.current = {
      id: assistantId,
      fullAnswer: suggestion.fullAnswer,
      shortAnswer: suggestion.shortAnswer,
    }
    setMessages((prev) => [
      ...prev,
      {
        id: assistantId,
        role: 'assistant',
        text: answerText,
        fullAnswer: suggestion.fullAnswer,
        shortAnswer: suggestion.shortAnswer,
        isChartDescription: true,
      },
    ])
    speak(answerText)
    setIsWaitingForAnswer(false)
  }

  // Quick summary / full detail toggle. If the current chart's
  // description has already been revealed in the chat, swap its text
  // live and re-speak it; otherwise this just changes what the next
  // suggestion click will use.
  const handleDepthChange = (newDepth) => {
    setDepth(newDepth)
    const revealed = lastRevealedRef.current
    if (!revealed) return

    const newText = newDepth === 'quick' ? revealed.shortAnswer : revealed.fullAnswer
    setMessages((prev) =>
      prev.map((m) => (m.id === revealed.id ? { ...m, text: newText } : m))
    )
    speak(newText)
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter') handleSend()
  }

  const handleMicToggle = () => {
    if (isListening) {
      stopListening()
    } else {
      startListening()
    }
  }

  // Re-speaks the most recent assistant message without asking anything
  // new — handy if the student missed it the first time.
  const handleRepeat = () => {
    const lastAssistant = [...messages].reverse().find((m) => m.role === 'assistant')
    if (lastAssistant) speak(lastAssistant.text)
  }

  const handleCopyTranscript = async () => {
    const transcriptText = messages
      .filter((m) => m.role === 'assistant' || m.role === 'user')
      .map((m) => `${m.role === 'assistant' ? 'Assistant' : 'You'}: ${m.text}`)
      .join('\n')

    try {
      await navigator.clipboard.writeText(transcriptText)
      setCopyStatus('copied')
      speak('Transcript copied')
      setTimeout(() => setCopyStatus('idle'), 2000)
    } catch (err) {
      console.error('Copy failed:', err)
    }
  }

  return (
    <div className="chat-panel">
      <h2 className="panel-title">Chat</h2>

      {hasChart && (
        <div className="depth-toggle" role="group" aria-label="Description detail level">
          <button
            type="button"
            className={`depth-btn ${depth === 'quick' ? 'depth-btn-active' : ''}`}
            aria-pressed={depth === 'quick'}
            onClick={() => handleDepthChange('quick')}
          >
            Quick summary
          </button>
          <button
            type="button"
            className={`depth-btn ${depth === 'full' ? 'depth-btn-active' : ''}`}
            aria-pressed={depth === 'full'}
            onClick={() => handleDepthChange('full')}
          >
            Full detail
          </button>
        </div>
      )}

      <div className="messages-list" role="log" aria-live="polite" aria-label="Conversation about the chart">
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`message message-${msg.role} ${msg.isCaveat ? 'message-caveat' : ''}`}
          >
            {msg.role === 'suggestion' ? (
              <button
                type="button"
                className="suggestion-chip"
                onClick={() => handleSuggestionClick(msg)}
              >
                <span className="message-role">Suggested question — tap to ask</span>
                {msg.prompt}
              </button>
            ) : (
              <span className="message-bubble">
                <span className="message-role">
                  {msg.isCaveat ? '⚠ Confidence check' : msg.role === 'assistant' ? 'Assistant' : 'You'}
                </span>
                {msg.text}
              </span>
            )}
          </div>
        ))}
        {isWaitingForAnswer && (
          <div className="message message-assistant" aria-live="polite">
            <span className="message-bubble message-bubble-thinking">
              <span className="message-role">Assistant</span>
              <span className="thinking-dots" aria-hidden="true">
                <span />
                <span />
                <span />
              </span>
              Thinking…
            </span>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      <div className="chat-controls" role="group" aria-label="Speech controls">
        <button
          type="button"
          onClick={stopSpeaking}
          disabled={!isSpeaking}
          className="control-btn"
        >
          ⏹ Stop speaking
        </button>

        <button type="button" onClick={handleRepeat} className="control-btn">
          🔁 Repeat
        </button>

        <button type="button" onClick={handleCopyTranscript} className="control-btn">
          📋 Copy transcript
        </button>
        {copyStatus === 'copied' && (
          <span className="copy-confirmation" role="status" aria-live="polite">
            Copied!
          </span>
        )}

        <fieldset className="rate-control">
          <legend className="visually-hidden">Speech rate</legend>
          {RATE_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              className={`rate-btn ${rate === opt.value ? 'rate-btn-active' : ''}`}
              aria-pressed={rate === opt.value}
              onClick={() => setRate(opt.value)}
            >
              {opt.label}
            </button>
          ))}
        </fieldset>
      </div>

      <div className="chat-input-row">
        <label htmlFor="question-input" className="visually-hidden">
          Ask a question about the chart
        </label>
        <input
          id="question-input"
          type="text"
          value={inputText}
          onChange={(e) => setInputText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask a question, e.g. 'which bar is highest?'"
        />

        <button
          onClick={handleMicToggle}
          aria-pressed={isListening}
          aria-label={isListening ? 'Stop listening' : 'Ask by voice'}
          className={`mic-btn ${isListening ? 'mic-active' : ''}`}
        >
          {isListening ? '● Listening…' : '🎤 Speak'}
        </button>

        <button
          onClick={handleSend}
          className="primary-btn"
          disabled={isWaitingForAnswer || !inputText.trim()}
        >
          Send
        </button>
      </div>
    </div>
  )
}
