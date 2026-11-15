# logstream

**A small real-time log anomaly detection service.** Send raw log lines over HTTP and get alerts back immediately, with a live dashboard. It turns the findings of my offline study [log-anomaly-bench](https://github.com/Zolnarb1/log-anomaly-bench) into something you could actually deploy.

![Dashboard](docs/dashboard.jpg)
*Replaying the BlueGene/L supercomputer log into the service. Alerts arrive as lines stream in.*

## What it detects
| Alert | Rule | Why (from the offline study) |
|---|---|---|
| `SEVERITY` | line carries `FATAL` / `CRITICAL` / `FAILURE`; at most one alert per template per minute | on BGL this single signal reached F1 0.80, better than any ML model I tested |
| `NEW_TEMPLATE` | Drain creates a template never seen before (after a warm-up) | 67% of anomalies in the test period came from templates unseen in training (concept drift) |
| `RATE_SPIKE` | a template's count this minute > mean + 4σ of its past minutes (min. 20) | catches floods of "normal-looking" messages |

Templates are mined online with [Drain3](https://github.com/logpai/Drain3). Numbers and hex values are masked, so `generating core.2275` and `generating core.862` become one template.

## API
| Method | Path | |
|---|---|---|
| `POST` | `/ingest` | `{"lines": ["raw line", ...], "timestamp": 1117838570}` → alerts raised by this batch |
| `GET` | `/alerts?limit=50&kind=SEVERITY` | recent alerts, newest first |
| `GET` | `/templates?top=20` | most frequent templates |
| `GET` | `/stats` | counters for the dashboard |
| `GET` | `/health` | liveness probe |
| `GET` | `/` | live dashboard (vanilla JS, polls every second) |

Interactive docs are at `/docs` (FastAPI / OpenAPI).

```bash
curl -X POST localhost:8000/ingest -H "Content-Type: application/json" \
     -d '{"lines": ["KERNEL FATAL data TLB error interrupt"]}'
```

## Run it
```bash
# local
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt && pip install -e .
uvicorn logstream.api:app --port 8000
python scripts/replay.py --speed 10        # in a 2nd terminal: stream a real log in
                                           # then open http://localhost:8000

# docker
docker build -t logstream .
docker run -p 8000:8000 logstream
```
Configuration via environment variables: `LOGSTREAM_WARMUP` (lines before new-template alerts start, default 500) and `LOGSTREAM_SPIKE_K` (σ multiplier, default 4).

## Engineering notes
- **Throughput:** the detector handles ~150k lines/s on one laptop thread (`scripts/bench.py`, 200k BGL lines, detection logic only, without HTTP).
- **Deterministic and testable:** time is passed into the detector instead of read from the clock. Rate statistics use Welford's online mean/variance (O(1) memory per template).
- **Thread safety:** Drain3 isn't thread-safe, so a lock wraps detector access in the API.
- **Bounded memory:** the alert buffer is capped (500), as is the per-minute dedup set.
- **Input validation:** Pydantic models reject malformed payloads with 422 and cap batch size (10,000 lines).
- **Tests** (`tests/`): each alert type, warm-up, per-minute dedup, rate-spike dedup, endpoint filters, bad payloads.
- **CI** (`.github/workflows/ci.yml`): runs the tests, builds the Docker image, starts it, and smoke-tests `/health` and `/ingest`.
- **Container:** non-root user, healthcheck, slim base image.

## Limitations / next steps
- State is in memory: a restart forgets learned templates. Next step: persist Drain state (Drain3 supports Redis/file persistence).
- Single process: scaling out would need a message queue (e.g. Kafka) and per-source detectors.
- No ground-truth evaluation in this repo. The offline evaluation lives in log-anomaly-bench.

## Author
Hamza Fakhri, Computer Science engineering student, Polytech Nantes.
