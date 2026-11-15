"""Measure detector throughput in-process (no HTTP), on a real log file.

Usage:  python scripts/bench.py --file data/BGL_head.log
"""
import argparse
import time
from pathlib import Path

from logstream.detector import StreamDetector


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--file", type=Path, default=Path("data/BGL_head.log"))
    a = p.parse_args()
    lines = []
    with open(a.file, encoding="utf-8-sig", errors="replace") as f:
        for line in f:
            parts = line.split(" ", 9)
            ts = float(parts[1]) if parts[1].isdigit() else 0.0
            lines.append((" ".join(parts[7:]).rstrip("\n"), ts))
    det = StreamDetector()
    t0 = time.perf_counter()
    for text, ts in lines:
        det.process(text, ts)
    dt = time.perf_counter() - t0
    s = det.stats()
    print(f"{len(lines):,} lines in {dt:.1f}s = {len(lines) / dt:,.0f} lines/s (single thread)")
    print(f"templates: {s['templates']}, alerts: {s['alerts_by_kind']}")


if __name__ == "__main__":
    main()
