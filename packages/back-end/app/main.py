import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .llm_agent import analyze_chart
from .models import AnalyzeChartRequest, AnalyzeChartResponse

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Diagram Reader API")

# Dev CORS: Vite serves the front-end on 5173. Tighten this before deploying.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def healthcheck():
    return {"status": "ok"}


@app.post("/analyze-chart", response_model=AnalyzeChartResponse)
async def analyze_chart_endpoint(payload: AnalyzeChartRequest) -> AnalyzeChartResponse:
    """Accepts a chart image (base64 data URL) plus an optional ChartTask,
    runs the Claude extraction agent, and returns the structured result."""
    return analyze_chart(payload)
