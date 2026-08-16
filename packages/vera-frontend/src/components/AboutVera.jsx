import { useEffect } from 'react'
import VeraLogo from './VeraLogo.jsx'

/**
 * AboutVera — the full-page explainer, reached via the "?" icon inside
 * VeraAssistant's header from any chat state. Not a small popover on
 * purpose: it's meant to read as "you've been taken to a page," not
 * "a tooltip appeared."
 */
export default function AboutVera({ onClose }) {
  // Let Escape close it too — this is effectively a full-screen dialog.
  useEffect(() => {
    const handleKeyDown = (e) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [onClose])

  return (
    <div className="about-overlay" role="dialog" aria-label="About VERA">
      <div className="about-inner">
        <div className="about-head">
          <div className="about-brand">
            <VeraLogo size={34} />
            <span className="about-brand-name">VERA</span>
          </div>
          <button className="btn btn-outline" onClick={onClose}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M6 6l12 12M18 6 6 18" />
            </svg>
            Close
          </button>
        </div>

        <div className="hero">
          <h1>
            VERA helps you understand charts and fill forms —{' '}
            <span className="accent-text">no typing needed.</span>
          </h1>
          <p>
            Designed especially for older adults to easily read charts, understand what
            they mean, and fill out forms stress-free.
          </p>
        </div>

        <div className="steps">
          <div className="steps-track" aria-hidden="true" />

          <div className="step-card">
            <div className="step-icon" style={{ background: 'var(--accent-wash)' }}>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M4 16l4.5-5 3 3.5L16 9l4 7" />
                <rect x="3" y="4" width="18" height="16" rx="2" />
              </svg>
            </div>
            <p className="step-title">1. Add your chart</p>
            <p className="step-body">Take a photo or add a chart, diagram, or document.</p>
          </div>

          <div className="step-arrow" aria-hidden="true">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M9 6l6 6-6 6" />
            </svg>
          </div>

          <div className="step-card">
            <div className="step-icon" style={{ background: 'var(--done-wash)' }}>
              <svg width="20" height="20" viewBox="0 0 24 24" fill="var(--done)" aria-hidden="true">
                <path d="M12 2.5l1.9 6.6 6.6 1.9-6.6 1.9L12 19.5l-1.9-6.6-6.6-1.9 6.6-1.9L12 2.5z" />
              </svg>
            </div>
            <p className="step-title">2. VERA reads it</p>
            <p className="step-body">Our helpful assistant automatically reads and extracts the data.</p>
          </div>

          <div className="step-arrow" aria-hidden="true">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M9 6l6 6-6 6" />
            </svg>
          </div>

          <div className="step-card">
            <div className="step-icon" style={{ background: 'var(--accent-wash)' }}>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M9 3h6l1 3H8l1-3Z" />
                <rect x="5" y="6" width="14" height="15" rx="2" />
              </svg>
            </div>
            <p className="step-title">3. Get your filled form</p>
            <p className="step-body">Your form is auto-filled and ready to save.</p>
          </div>
        </div>
      </div>
    </div>
  )
}
