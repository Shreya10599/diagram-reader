/**
 * VeraLogo — the one brand mark, used identically everywhere it appears
 * (landing header, chat panel header, About page, floating button proof
 * shots). Colors are fixed and defined in index.css as --logo-bg/1/2/3 —
 * they never change with the light/dark theme, same as an app icon
 * wouldn't. Don't recolor this per-background; if it needs to sit on a
 * colored bar (like the chat header), the fixed dark badge already
 * provides its own contrast.
 */
export default function VeraLogo({ size = 40, className = '' }) {
  return (
    <div
      className={`vera-logo ${className}`}
      style={{ width: size, height: size }}
      aria-hidden="true"
    >
      <svg width={size * 0.58} height={size * 0.58} viewBox="0 0 24 24">
        <circle cx="12" cy="8.3" r="5.1" fill="var(--logo-1)" />
        <circle cx="8.3" cy="14.6" r="5.1" fill="var(--logo-2)" />
        <circle cx="15.7" cy="14.6" r="5.1" fill="var(--logo-3)" />
      </svg>
    </div>
  )
}
