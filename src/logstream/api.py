"""HTTP API around the online detector.

Endpoints
  POST /ingest         {"lines": ["raw log line", ...]}  -> alerts raised by this batch
  GET  /alerts?limit=  most recent alerts (newest first)
  GET  /templates?top= most frequent templates
  GET  /stats          counters for the dashboard
  POST /snapshot       save the detector state now (if persistence is on)
  GET  /health         liveness probe
  GET  /               live dashboard (static HTML)

Persistence (optional): set LOGSTREAM_STATE=/path/state.json. The state is loaded at startup,
saved at most every LOGSTREAM_SAVE_EVERY seconds while ingesting (default 30), and on shutdown.

Run:  uvicorn logstream.api:app --reload
"""
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from logstream.detector import StreamDetector

STATIC = Path(__file__).parent / "static"
log = logging.getLogger("logstream")


class Batch(BaseModel):
    lines: list[str] = Field(..., max_length=10_000, description="raw log lines")
    timestamp: float | None = Field(None, description="event time (unix s); default: now")


def _settings() -> dict:
    return {"warmup_lines": int(os.getenv("LOGSTREAM_WARMUP", "500")),
            "spike_k": float(os.getenv("LOGSTREAM_SPIKE_K", "4"))}


def _load_or_fresh(state_path: Path | None) -> tuple[StreamDetector, bool]:
    """Restore from the state file if there is one. A corrupt file is set aside, not fatal."""
    if state_path is None or not state_path.exists():
        return StreamDetector(**_settings()), False
    try:
        det = StreamDetector.load(state_path, **_settings())
        log.info("restored state: %s lines, %s templates", det.lines_seen, len(det.miner.drain.clusters))
        return det, True
    except Exception as exc:                                    # noqa: BLE001
        bad = state_path.with_suffix(state_path.suffix + ".corrupt")
        os.replace(state_path, bad)
        log.warning("could not load %s (%s); moved it to %s and started fresh", state_path, exc, bad)
        return StreamDetector(**_settings()), False


def create_app(detector: StreamDetector | None = None,
               state_path: str | Path | None = None,
               save_every: float | None = None) -> FastAPI:
    if state_path is None and os.getenv("LOGSTREAM_STATE"):
        state_path = os.environ["LOGSTREAM_STATE"]
    state_path = Path(state_path) if state_path else None
    if save_every is None:
        save_every = float(os.getenv("LOGSTREAM_SAVE_EVERY", "30"))

    if detector is not None:
        det, restored = detector, False
    else:
        det, restored = _load_or_fresh(state_path)
    lock = Lock()                                   # Drain is not thread-safe
    last_save = [time.monotonic()]

    def save() -> bool:                             # caller holds the lock
        if state_path is None:
            return False
        det.save(state_path)
        last_save[0] = time.monotonic()
        return True

    @asynccontextmanager
    async def lifespan(_app):
        yield
        with lock:
            if save():
                log.info("state saved to %s on shutdown", state_path)

    app = FastAPI(title="logstream", version="0.2.0", lifespan=lifespan,
                  description="Real-time log anomaly detection (Drain + severity, new-template "
                              "and rate-spike alerts), with optional state persistence")

    @app.post("/ingest")
    def ingest(batch: Batch):
        now = batch.timestamp if batch.timestamp is not None else time.time()
        with lock:
            alerts = [a.to_dict() for line in batch.lines for a in det.process(line, now)]
            if state_path is not None and time.monotonic() - last_save[0] >= save_every:
                save()
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
            return {**det.stats(), "persistence": state_path is not None,
                    "restored_from_state": restored}

    @app.post("/snapshot")
    def snapshot():
        if state_path is None:
            raise HTTPException(409, "persistence is off (set LOGSTREAM_STATE)")
        with lock:
            save()
        return {"saved_to": str(state_path), "lines_seen": det.lines_seen}

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    def dashboard():
        return FileResponse(STATIC / "index.html")

    return app


app = create_app()
