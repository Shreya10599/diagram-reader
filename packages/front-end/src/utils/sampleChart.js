/**
 * sampleChart.js — generates a synthetic "photo of a chart" entirely
 * client-side, so the "try a sample chart" demo button can send a real
 * image through the real /analyze-chart pipeline instead of returning
 * canned data. No new asset file, no backend involvement here.
 *
 * Approach: build a standalone SVG string (own <rect> background, no CSS
 * custom properties — those don't resolve once the SVG is loaded into an
 * off-DOM <img>, so colors are hardcoded hex here rather than reusing
 * PieChart.jsx's var(--chart-N)), then rasterize it to a PNG via an
 * offscreen canvas. White background on purpose: this is meant to read
 * like a printed/screenshotted chart, not this app's dark UI theme.
 */

const SLICE_COLORS = ['#3f6fd1', '#e07b39', '#3f9e52', '#d1a417', '#8b5fbf']

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

function buildPieSvgMarkup(series, title, size = 480) {
  const total = series.reduce((sum, s) => sum + s.value, 0)
  const cx = size / 2
  const cy = size / 2 + 30
  const r = size * 0.32
  const labelRadius = r * 0.62
  let cursor = 0

  const slices = series.map((s, i) => {
    const startAngle = (cursor / total) * 360
    cursor += s.value
    const endAngle = (cursor / total) * 360
    const midAngle = (startAngle + endAngle) / 2
    return {
      path: arcPath(cx, cy, r, startAngle, endAngle),
      color: SLICE_COLORS[i % SLICE_COLORS.length],
      pct: Math.round((s.value / total) * 100),
      labelPos: polarToCartesian(cx, cy, labelRadius, midAngle),
    }
  })

  const slicePaths = slices
    .map((s) => `<path d="${s.path}" fill="${s.color}" stroke="#ffffff" stroke-width="3" />`)
    .join('')

  const sliceLabels = slices
    .map(
      (s) =>
        `<text x="${s.labelPos.x}" y="${s.labelPos.y}" text-anchor="middle" ` +
        `dominant-baseline="middle" font-size="20" font-family="Arial, sans-serif" ` +
        `font-weight="bold" fill="#ffffff">${s.pct}%</text>`
    )
    .join('')

  const legendItems = series
    .map(
      (s, i) =>
        `<rect x="24" y="${70 + i * 34}" width="18" height="18" fill="${SLICE_COLORS[i % SLICE_COLORS.length]}" />` +
        `<text x="50" y="${84 + i * 34}" font-size="18" font-family="Arial, sans-serif" fill="#1a1a1a">${s.label}</text>`
    )
    .join('')

  return `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
    <rect width="${size}" height="${size}" fill="#ffffff" />
    <text x="${size / 2}" y="36" text-anchor="middle" font-size="24" font-family="Arial, sans-serif" font-weight="bold" fill="#1a1a1a">${title}</text>
    ${slicePaths}
    ${sliceLabels}
    ${legendItems}
  </svg>`
}

/**
 * Renders `series` as a pie chart and returns a PNG data URL — same
 * format `canvas.toDataURL()` produces for a real camera capture, so it
 * flows through the exact same code path as a real photo from here on.
 */
export function generateSampleChartImage(series, title) {
  const svgMarkup = buildPieSvgMarkup(series, title)
  const svgBlob = new Blob([svgMarkup], { type: 'image/svg+xml' })
  const url = URL.createObjectURL(svgBlob)

  return new Promise((resolve, reject) => {
    const img = new Image()
    img.onload = () => {
      const canvas = document.createElement('canvas')
      canvas.width = img.width
      canvas.height = img.height
      const ctx = canvas.getContext('2d')
      ctx.drawImage(img, 0, 0)
      URL.revokeObjectURL(url)
      resolve(canvas.toDataURL('image/png'))
    }
    img.onerror = (err) => {
      URL.revokeObjectURL(url)
      reject(err)
    }
    img.src = url
  })
}
