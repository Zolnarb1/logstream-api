"""HTTP API around the online detector.

Endpoints
  POST /ingest         {"lines": ["raw log line", ...]}  -> alerts raised by this batch
  GET  /alerts?limit=  most recent alerts (newest first)
  GET  /templates?top= most frequent templates
  GET  /stats          counters for the dashboard
  GET  /health         liveness probe
  GET  /               live dashboard (static HTML)

Run:  uvicorn logstream.api:app --reload
"""
import os
import time
from pathlib import Path
from threading import Lock

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from logstream.detector import StreamDetector

STATIC = Path(__file__).parent / "static"


class Batch(BaseModel):
    lines: list[str] = Field(..., max_length=10_000, description="raw log lines")
    timestamp: float | None = Field(None, description="event time (unix s); default: now")


def create_app(detector: StreamDetector | None = None) -> FastAPI:
    det = detector or StreamDetector(
        warmup_lines=int(os.getenv("LOGSTREAM_WARMUP", "500")),
        spike_k=float(os.getenv("LOGSTREAM_SPIKE_K", "4")),
    )
    lock = Lock()                                   # Drain is not thread-safe
    app = FastAPI(title="logstream", version="0.1.0",
                  description="Real-time log anomaly detection (Drain + severity, new-template "
                              "and rate-spike alerts)")

    @app.post("/ingest")
    def ingest(batch: Batch):
        now = batch.timestamp if batch.timestamp is not None else time.time()
        with lock:
            alerts = [a.to_dict() for line in batch.lines for a in det.process(line, now)]
        return {"accepted": len(batch.lines), "alerts": alerts}

    @app.get("/alerts")
    def alerts(limit: int = Query(50, ge=1, le=500), kind: str | None = None):
        with lock:
            items = [a.to_dict() for a in reversed(det.alerts) if kind is None or a.kind == kind]
        return items[:limit]

    @app.get("/templates")
    def templates(top: int = Query(20, ge=1, le=200)):
        with lock:
            return det.templates(top)

    @app.get("/stats")
    def stats():
        with lock:
            return det.stats()

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    def dashboard():
        return FileResponse(STATIC / "index.html")

    return app


app = create_app()
