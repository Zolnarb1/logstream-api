"""Online log anomaly detector: everything happens as lines arrive, no batch training.

Three alert types, chosen from what worked in log-anomaly-bench (offline study on BGL):
  SEVERITY      the line carries a FATAL / CRITICAL / FAILURE level (best single signal on BGL)
  NEW_TEMPLATE  Drain creates a template never seen before, after a warm-up period
                (new failure modes are what supervised models miss: concept drift)
  RATE_SPIKE    a template appears far more often in the current minute than usual
                (count > mean + k*std of its previous minutes, with an absolute floor)

Time is passed in explicitly so the detector is deterministic and testable.
"""
import re
from collections import Counter, deque
from dataclasses import asdict, dataclass, field

from drain3 import TemplateMiner
from drain3.masking import MaskingInstruction
from drain3.template_miner_config import TemplateMinerConfig

LEVEL_RE = re.compile(r"\b(FATAL|CRITICAL|FAILURE|SEVERE|ERROR|WARNING|WARN|INFO|DEBUG)\b")
SEVERE = {"FATAL", "CRITICAL", "FAILURE"}


@dataclass
class Alert:
    kind: str
    time: float
    template_id: int
    template: str
    line: str
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TemplateStats:
    """Per-template minute counts, with a running mean/variance (Welford) of past minutes."""
    current_minute: int = -1
    current_count: int = 0
    n: int = 0
    mean: float = 0.0
    m2: float = 0.0

    def close_minute(self) -> None:
        self.n += 1
        delta = self.current_count - self.mean
        self.mean += delta / self.n
        self.m2 += delta * (self.current_count - self.mean)

    @property
    def std(self) -> float:
        return (self.m2 / (self.n - 1)) ** 0.5 if self.n > 1 else 0.0


@dataclass
class StreamDetector:
    warmup_lines: int = 500
    spike_k: float = 4.0
    spike_min_count: int = 20
    max_alerts: int = 500
    lines_seen: int = 0
    level_counts: Counter = field(default_factory=Counter)
    template_counts: Counter = field(default_factory=Counter)
    alerts: deque = field(default_factory=deque)
    alert_totals: Counter = field(default_factory=Counter)
    _stats: dict = field(default_factory=dict)
    _spiked: set = field(default_factory=set)
    _severity_seen: set = field(default_factory=set)

    def __post_init__(self):
        cfg = TemplateMinerConfig()
        cfg.masking_instructions = [MaskingInstruction(r"0x[0-9a-fA-F]+", "HEX"),
                                    MaskingInstruction(r"\b\d+\b", "NUM")]
        cfg.drain_sim_th = 0.5
        cfg.profiling_enabled = False
        self.miner = TemplateMiner(config=cfg)

    # ------------------------------------------------------------------ ingestion
    def process(self, line: str, now: float) -> list[Alert]:
        line = line.rstrip("\n")
        if not line:
            return []
        self.lines_seen += 1
        m = LEVEL_RE.search(line)
        level = m.group(1) if m else "UNKNOWN"
        self.level_counts[level] += 1

        result = self.miner.add_log_message(line)
        tid = result["cluster_id"]
        template = result["template_mined"]
        self.template_counts[tid] += 1
        new: list[Alert] = []

        if level in SEVERE and (tid, int(now // 60)) not in self._severity_seen:
            # one SEVERITY alert per template per minute (paging on every line is useless)
            self._severity_seen.add((tid, int(now // 60)))
            new.append(Alert("SEVERITY", now, tid, template, line, level))
        if result["change_type"] == "cluster_created" and self.lines_seen > self.warmup_lines:
            new.append(Alert("NEW_TEMPLATE", now, tid, template, line,
                             f"first seen after {self.lines_seen:,} lines"))
        spike = self._update_rate(tid, now)
        if spike:
            new.append(Alert("RATE_SPIKE", now, tid, template, line, spike))

        if len(self._severity_seen) > 50_000:          # bound memory on long runs
            self._severity_seen.clear()
        for a in new:
            self.alert_totals[a.kind] += 1
            self.alerts.append(a)
            while len(self.alerts) > self.max_alerts:
                self.alerts.popleft()
        return new

    def _update_rate(self, tid: int, now: float) -> str | None:
        minute = int(now // 60)
        s = self._stats.setdefault(tid, TemplateStats(current_minute=minute))
        if minute != s.current_minute:
            s.close_minute()
            # minutes with zero occurrences in between also count as observations
            for _ in range(min(minute - s.current_minute - 1, 60)):
                s.current_count = 0
                s.close_minute()
            s.current_minute, s.current_count = minute, 0
            self._spiked.discard(tid)
        s.current_count += 1
        threshold = s.mean + self.spike_k * s.std
        if (s.n >= 5 and s.current_count >= self.spike_min_count
                and s.current_count > threshold and tid not in self._spiked):
            self._spiked.add(tid)                      # one alert per template per minute
            return f"{s.current_count} this minute vs usual {s.mean:.1f} ± {s.std:.1f}"
        return None

    # ------------------------------------------------------------------ queries
    def templates(self, top: int = 20) -> list[dict]:
        clusters = {c.cluster_id: c.get_template() for c in self.miner.drain.clusters}
        return [{"template_id": tid, "count": n, "template": clusters.get(tid, "")}
                for tid, n in self.template_counts.most_common(top)]

    def stats(self) -> dict:
        return {"lines_seen": self.lines_seen,
                "templates": len(self.miner.drain.clusters),
                "levels": dict(self.level_counts),
                "alerts_by_kind": dict(self.alert_totals),
                "warming_up": self.lines_seen <= self.warmup_lines}
