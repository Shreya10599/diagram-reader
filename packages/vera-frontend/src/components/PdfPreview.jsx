/**
 * PdfPreview — the right-hand panel of the workspace. Shows whatever
 * PDF form the user has uploaded via VERA's "Upload a form" option.
 * Completely independent from LandingForm/the chart-fill flow — a PDF
 * upload only ever changes what's shown here, never the web form's
 * fields, and vice versa.
 */
export default function PdfPreview({ pdfUrl }) {
  return (
    <div className="pdf-panel">
      <p className="panel-heading">Form preview</p>

      {pdfUrl ? (
        <iframe src={pdfUrl} title="Your uploaded form" className="pdf-frame" />
      ) : (
        <div className="pdf-empty">
          <svg
            width="34"
            height="34"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M9 3h6l1 3H8l1-3Z" />
            <rect x="5" y="6" width="14" height="15" rx="2" />
          </svg>
          <p>No form uploaded yet</p>
          <p className="pdf-empty-hint">Ask VERA to "Upload a form" and it'll appear here.</p>
        </div>
      )}
    </div>
  )
}
