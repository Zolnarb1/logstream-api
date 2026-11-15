"""Measure state persistence on a real log: file size, save time, restore time.

Usage:  python scripts/bench_state.py --file data/BGL_head.log
"""
import argparse
import tempfile
import time
from pathlib import Path

from logstream.detector import StreamDetector


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--file", type=Path, default=Path("data/BGL_head.log"))
    a = p.parse_args()
    det = StreamDetector()
    with open(a.file, encoding="utf-8-sig", errors="replace") as f:
        for line in f:
            parts = line.split(" ", 9)
            ts = float(parts[1]) if parts[1].isdigit() else 0.0
            det.process(" ".join(parts[7:]).rstrip("\n"), ts)

    path = Path(tempfile.mkdtemp()) / "state.json"
    t0 = time.perf_counter()
    det.save(path)
    t_save = time.perf_counter() - t0
    t0 = time.perf_counter()
    det2 = StreamDetector.load(path)
    t_load = time.perf_counter() - t0
    assert det2.stats() == det.stats()

    s = det.stats()
    print(f"{s['lines_seen']:,} lines, {s['templates']} templates")
    print(f"state file: {path.stat().st_size / 1024:.0f} KiB, save {t_save * 1000:.0f} ms, "
          f"restore {t_load * 1000:.0f} ms (restored stats identical)")


if __name__ == "__main__":
    main()
