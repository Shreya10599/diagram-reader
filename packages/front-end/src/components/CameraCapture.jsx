import { useRef, useState, useCallback } from 'react'
import PieChart from './PieChart.jsx'
import { DEMO_PIE_SERIES } from '../mockApi.js'

/**
 * CameraCapture — opens the device camera, lets the user snap a photo
 * of a printed chart/diagram, and hands the captured image (as a base64
 * JPEG string) up to the parent via onCapture. Also offers a "sample
 * chart" demo button (onDemo) that runs the same analyze flow with no
 * photo needed, and shows a small preview + reset control once a chart
 * has been analyzed.
 *
 * How the camera part works, step by step (useful if you're rusty on this):
 * 1. <video> shows a live camera feed (via getUserMedia).
 * 2. When the student taps "Capture", we draw the current video frame
 *    onto a hidden <canvas>.
 * 3. We read the canvas back out as a base64 image string — that's the
 *    format the backend/Claude Vision API expects.
 */
export default function CameraCapture({
  onCapture,
  onDemo,
  isAnalyzing,
  capturedImage,
  hasResult,
  onReset,
  speak,
}) {
  const videoRef = useRef(null)
  const canvasRef = useRef(null)
  const streamRef = useRef(null)
  const [isCameraOn, setIsCameraOn] = useState(false)
  const [error, setError] = useState(null)

  const startCamera = useCallback(async () => {
    setError(null)
    try {
      // facingMode: 'environment' -> use the back camera on a phone,
      // since that's the one pointed at a textbook page.
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'environment' },
        audio: false,
      })
      streamRef.current = stream
      if (videoRef.current) {
        videoRef.current.srcObject = stream
      }
      setIsCameraOn(true)
    } catch (err) {
      console.error('Camera access failed:', err)
      const errorMsg =
        'Could not access the camera. Check browser permissions and try again.'
      setError(errorMsg)
      speak(errorMsg) // status text alone isn't enough for a blind user
    }
  }, [speak])

  const stopCamera = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop())
    setIsCameraOn(false)
  }, [])

  const capturePhoto = useCallback(() => {
    const video = videoRef.current
    const canvas = canvasRef.current
    if (!video || !canvas) return

    canvas.width = video.videoWidth
    canvas.height = video.videoHeight
    const ctx = canvas.getContext('2d')
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height)

    // toDataURL gives us "data:image/jpeg;base64,....." — strip the
    // prefix if your backend just wants the raw base64 payload.
    const imageDataUrl = canvas.toDataURL('image/jpeg', 0.9)

    onCapture(imageDataUrl)
    stopCamera()
  }, [onCapture, stopCamera])

  // Fallback: let the student pick an existing photo instead of the
  // live camera. Useful for testing on desktop, and as a backup during
  // the demo if the live camera flakes out.
  const handleFileUpload = useCallback(
    (event) => {
      const file = event.target.files?.[0]
      if (!file) return
      const reader = new FileReader()
      reader.onload = () => onCapture(reader.result)
      reader.readAsDataURL(file)
      event.target.value = '' // allow re-selecting the same file later
    },
    [onCapture]
  )

  return (
    <div className="camera-capture">
      <h2 className="panel-title">Your chart</h2>

      {error && (
        <p role="alert" className="error-text">
          {error}
        </p>
      )}

      {hasResult ? (
        <div className="result-preview">
          <img
            src={capturedImage}
            alt="The chart you captured, uploaded, or generated via the sample-chart demo"
            className="preview-img"
          />
          <button onClick={onReset} className="secondary-btn">
            Try another chart
          </button>
        </div>
      ) : !isCameraOn ? (
        <div className="camera-controls">
          <button
            onClick={startCamera}
            className="primary-btn"
            disabled={isAnalyzing}
          >
            📷 Open Camera
          </button>

          <label className="upload-card">
            <span className="upload-icon" aria-hidden="true">
              🖼️
            </span>
            <span className="upload-card-text">
              <strong>Choose a photo</strong>
              <span>Pick an existing image from your device</span>
            </span>
            <input
              type="file"
              accept="image/*"
              onChange={handleFileUpload}
              disabled={isAnalyzing}
              aria-label="Upload a photo of a chart or diagram"
            />
          </label>

          <button
            type="button"
            className="demo-card"
            onClick={onDemo}
            disabled={isAnalyzing}
          >
            <PieChart series={DEMO_PIE_SERIES} size={56} showLabels={false} />
            <span className="upload-card-text">
              <strong>Try a sample chart</strong>
              <span>See how it works — no photo needed</span>
            </span>
          </button>
        </div>
      ) : (
        <div className="camera-live">
          <video
            ref={videoRef}
            autoPlay
            playsInline
            aria-label="Live camera preview"
          />
          <button onClick={capturePhoto} className="primary-btn capture-btn">
            Capture Photo
          </button>
          <button onClick={stopCamera} className="secondary-btn">
            Cancel
          </button>
        </div>
      )}

      {isAnalyzing && (
        <p role="status" aria-live="polite" className="analyzing-status">
          <span className="spinner" aria-hidden="true" />
          Analyzing the image…
        </p>
      )}

      {/* Hidden canvas used only to grab a still frame — never shown. */}
      <canvas ref={canvasRef} style={{ display: 'none' }} />
    </div>
  )
}
