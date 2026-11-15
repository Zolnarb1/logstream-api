from fastapi.testclient import TestClient

from logstream.api import create_app
from logstream.detector import StreamDetector


def make_client(**kw):
    det = StreamDetector(warmup_lines=kw.pop("warmup", 10), **kw)
    return TestClient(create_app(det)), det


def test_health_and_dashboard():
    client, _ = make_client()
    assert client.get("/health").json() == {"status": "ok"}
    assert "logstream" in client.get("/").text


def test_severity_alert():
    client, _ = make_client()
    r = client.post("/ingest", json={"lines": ["RAS KERNEL FATAL data TLB error interrupt"], "timestamp": 0})
    kinds = [a["kind"] for a in r.json()["alerts"]]
    assert "SEVERITY" in kinds


def test_new_template_only_after_warmup():
    client, det = make_client(warmup=5)
    warm = [f"user {i} logged in from host {i}" for i in range(5)]
    r = client.post("/ingest", json={"lines": warm + ["brand new message about disk controller"], "timestamp": 0})
    kinds = [a["kind"] for a in r.json()["alerts"]]
    assert kinds == ["NEW_TEMPLATE"]                     # the first 5 lines are warm-up
    assert det.stats()["templates"] == 2                 # numbers masked -> 1 template + the new one


def test_rate_spike():
    client, _ = make_client(spike_min_count=20)
    # 10 quiet minutes: 2 occurrences per minute
    for minute in range(10):
        client.post("/ingest", json={"lines": ["cache flush done"] * 2, "timestamp": minute * 60})
    # then 50 in one minute
    r = client.post("/ingest", json={"lines": ["cache flush done"] * 50, "timestamp": 10 * 60})
    spikes = [a for a in r.json()["alerts"] if a["kind"] == "RATE_SPIKE"]
    assert len(spikes) == 1                              # one alert, not one per line


def test_alerts_endpoint_filters_and_limits():
    client, _ = make_client()
    for minute in range(30):
        client.post("/ingest", json={"lines": [f"node {minute} FATAL kernel panic"], "timestamp": minute * 60})
    assert len(client.get("/alerts?limit=5").json()) == 5
    assert all(a["kind"] == "SEVERITY" for a in client.get("/alerts?kind=SEVERITY").json())


def test_severity_deduplicated_per_template_per_minute():
    client, det = make_client()
    r = client.post("/ingest", json={"lines": ["kernel FATAL panic"] * 100, "timestamp": 0})
    assert sum(a["kind"] == "SEVERITY" for a in r.json()["alerts"]) == 1
    assert det.stats()["alerts_by_kind"]["SEVERITY"] == 1


def test_rejects_bad_payload():
    client, _ = make_client()
    assert client.post("/ingest", json={"nope": []}).status_code == 422
