"""A restart must not make the detector forget what it learned."""
from fastapi.testclient import TestClient

from logstream.api import create_app
from logstream.detector import StreamDetector

WARM = [f"user {i} logged in from host {i}" for i in range(20)]


def test_roundtrip_keeps_templates_and_counters(tmp_path):
    det = StreamDetector(warmup_lines=10)
    for line in WARM + ["disk controller reset on bus 3", "kernel FATAL panic"]:
        det.process(line, 0)
    det.save(tmp_path / "state.json")

    det2 = StreamDetector.load(tmp_path / "state.json", warmup_lines=10)
    assert det2.stats() == det.stats()
    assert det2.templates() == det.templates()
    assert [a.to_dict() for a in det2.alerts] == [a.to_dict() for a in det.alerts]


def test_known_template_is_not_new_after_restart(tmp_path):
    det = StreamDetector(warmup_lines=10)
    for line in WARM + ["disk controller reset on bus 3"]:
        det.process(line, 0)
    det.save(tmp_path / "s.json")
    det2 = StreamDetector.load(tmp_path / "s.json", warmup_lines=10)

    # seen before the restart -> same template, no alert
    assert det2.process("disk controller reset on bus 7", 60) == []
    # genuinely new -> alert straight away (warm-up is not repeated after a restart)
    kinds = [a.kind for a in det2.process("fan speed sensor unreachable", 60)]
    assert kinds == ["NEW_TEMPLATE"]


def test_rate_statistics_survive_restart(tmp_path):
    det = StreamDetector(warmup_lines=0, spike_min_count=20)
    for minute in range(10):
        for _ in range(2):
            det.process("cache flush done", minute * 60)
    det.save(tmp_path / "s.json")
    det2 = StreamDetector.load(tmp_path / "s.json", warmup_lines=0, spike_min_count=20)

    alerts = [a for _ in range(50) for a in det2.process("cache flush done", 10 * 60)]
    assert [a.kind for a in alerts] == ["RATE_SPIKE"]


def test_app_saves_on_shutdown_and_restores_on_startup(tmp_path):
    path = tmp_path / "state.json"
    with TestClient(create_app(state_path=path)) as client:      # context manager runs lifespan
        client.post("/ingest", json={"lines": WARM, "timestamp": 0})
        assert client.get("/stats").json()["restored_from_state"] is False
    assert path.exists()

    with TestClient(create_app(state_path=path)) as client:
        stats = client.get("/stats").json()
        assert stats["restored_from_state"] is True
        assert stats["lines_seen"] == len(WARM)


def test_snapshot_endpoint(tmp_path):
    path = tmp_path / "state.json"
    with TestClient(create_app(state_path=path)) as client:
        client.post("/ingest", json={"lines": ["a b c"], "timestamp": 0})
        r = client.post("/snapshot")
        assert r.status_code == 200 and path.exists()
    with TestClient(create_app(StreamDetector())) as client:
        assert client.post("/snapshot").status_code == 409       # persistence off


def test_corrupt_state_file_is_set_aside(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    with TestClient(create_app(state_path=path)) as client:
        assert client.get("/stats").json()["lines_seen"] == 0
    assert (tmp_path / "state.json.corrupt").exists()
