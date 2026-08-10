const SLICE_COLORS = [
  'var(--chart-1)',
  'var(--chart-2)',
  'var(--chart-3)',
  'var(--chart-4)',
  'var(--chart-5)',
]

function polarToCartesian(cx, cy, r, angleDeg) {
  const rad = ((angleDeg - 90) * Math.PI) / 180
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) }
}

function arcPath(cx, cy, r, startAngle, endAngle) {
  const start = polarToCartesian(cx, cy, r, endAngle)
  const end = polarToCartesian(cx, cy, r, startAngle)
  const largeArcFlag = endAngle - startAngle <= 180 ? '0' : '1'
  return `M ${cx} ${cy} L ${start.x} ${start.y} A ${r} ${r} 0 ${largeArcFlag} 0 ${end.x} ${end.y} Z`
}

/**
 * PieChart — a small, self-contained static pie chart (plain SVG, no
 * charting library needed). Used for the "sample chart" demo so there's
 * something real to look at/click before a backend is wired up.
 *
 * `series` is [{ label, value }] — values are treated as parts of a
 * whole and converted to percentages here, so callers can pass raw
 * counts or already-percentage values either way.
 */
export default function PieChart({ series, size = 200, showLabels = true }) {
  const total = series.reduce((sum, s) => sum + s.value, 0)
  const r = size / 2
  const labelRadius = r * 0.64
  let cursor = 0

  const slices = series.map((s, i) => {
    const startAngle = (cursor / total) * 360
    cursor += s.value
    const endAngle = (cursor / total) * 360
    const midAngle = (startAngle + endAngle) / 2
    return {
      key: s.label,
      path: arcPath(r, r, r, startAngle, endAngle),
      color: SLICE_COLORS[i % SLICE_COLORS.length],
      pct: Math.round((s.value / total) * 100),
      labelPos: polarToCartesian(r, r, labelRadius, midAngle),
    }
  })

  const altText = series
    .map((s) => `${s.label} ${Math.round((s.value / total) * 100)}%`)
    .join(', ')

  return (
    <svg
      className="pie-chart"
      viewBox={`0 0 ${size} ${size}`}
      width={size}
      height={size}
      role="img"
      aria-label={`Pie chart: ${altText}`}
    >
      {slices.map((slice) => (
        <path
          key={slice.key}
          d={slice.path}
          fill={slice.color}
          stroke="var(--color-surface)"
          strokeWidth="2"
        />
      ))}
      {showLabels &&
        slices.map((slice) => (
          <text
            key={slice.key}
            x={slice.labelPos.x}
            y={slice.labelPos.y}
            textAnchor="middle"
            dominantBaseline="middle"
            className="pie-chart-label"
          >
            {slice.pct}%
          </text>
        ))}
    </svg>
  )
}
