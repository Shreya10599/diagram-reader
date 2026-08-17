/**
 * LandingForm — the web form VERA fills out: Name/Address/Minimum/
 * Maximum/Average. Disabled and empty until `fields` comes in from a
 * chart upload (see App.jsx / VeraAssistant's chart-upload path only —
 * PDF uploads never touch this) — once filled, Minimum/Maximum/Average
 * switch to the green "AI-filled" styling and every field becomes
 * editable, so the person can review and correct before saving.
 *
 * The VERA logo + heading used to live here but now sits above the
 * whole two-column workspace in App.jsx, since this is just one of the
 * two panels now.
 */
export default function LandingForm({ fields, onFieldChange }) {
  const filled = Boolean(fields)

  return (
    <div className="form-panel">
        <div className="form-field">
          <label className="form-label" htmlFor="field-name">
            Name
          </label>
          <input
            id="field-name"
            className="form-input"
            placeholder="e.g. Jane Doe"
            value={fields?.name ?? ''}
            onChange={(e) => onFieldChange('name', e.target.value)}
            disabled={!filled}
          />
        </div>

        <div className="form-field">
          <label className="form-label" htmlFor="field-address">
            Address
          </label>
          <input
            id="field-address"
            className="form-input"
            placeholder="e.g. 123 Main St, Springfield"
            value={fields?.address ?? ''}
            onChange={(e) => onFieldChange('address', e.target.value)}
            disabled={!filled}
          />
        </div>

        <div className="form-field">
          <label className="form-label" htmlFor="field-min">
            Minimum value
          </label>
          <input
            id="field-min"
            className={`form-input ${filled ? 'is-ai-fill' : ''}`}
            placeholder="e.g. 0"
            value={fields?.min ?? ''}
            onChange={(e) => onFieldChange('min', e.target.value)}
            disabled={!filled}
          />
        </div>

        <div className="form-field">
          <label className="form-label" htmlFor="field-max">
            Maximum value
          </label>
          <input
            id="field-max"
            className={`form-input ${filled ? 'is-ai-fill' : ''}`}
            placeholder="e.g. 100"
            value={fields?.max ?? ''}
            onChange={(e) => onFieldChange('max', e.target.value)}
            disabled={!filled}
          />
        </div>

        <div className="form-field">
          <label className="form-label" htmlFor="field-average">
            Average
          </label>
          <input
            id="field-average"
            className={`form-input ${filled ? 'is-ai-fill' : ''}`}
            placeholder="Calculated automatically"
            value={fields?.average ?? ''}
            onChange={(e) => onFieldChange('average', e.target.value)}
            disabled={!filled}
          />
          <p className="form-hint">
            <svg
              width="12"
              height="12"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <circle cx="12" cy="12" r="9" />
              <path d="M12 16v-4M12 8h.01" />
            </svg>
            The three green fields fill in automatically once VERA reads your chart.
          </p>
        </div>
    </div>
  )
}
