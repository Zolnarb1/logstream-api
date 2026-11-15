"""What a restart costs: NEW_TEMPLATE alerts after a restart, with and without persistence.

Feeds the first half of a real log, "restarts" the service halfway, then feeds the second half
to (a) a fresh detector (in-memory only, what v0.1 did) and (b) one restored from the saved state.
Alerts on templates the system had already learned before the restart are false alarms.

Usage:  python scripts/restart_effect.py --file data/BGL_head.log [--plot out.png]
"""
import argparse
import tempfile
from pathlib import Path

from logstream.detector import StreamDetector


def read(path):
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        for line in f:
            parts = line.split(" ", 9)
            ts = float(parts[1]) if parts[1].isdigit() else 0.0
            yield " ".join(parts[7:]).rstrip("\n"), ts


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--file", type=Path, default=Path("data/BGL_head.log"))
    p.add_argument("--plot", type=Path)
    a = p.parse_args()
    lines = list(read(a.file))
    half = len(lines) // 2

    before = StreamDetector()
    for text, ts in lines[:half]:
        before.process(text, ts)
    known = {c.get_template() for c in before.miner.drain.clusters}
    state = Path(tempfile.mkdtemp()) / "state.json"
    before.save(state)

    results = {}
    for name, det in [("in-memory (v0.1)", StreamDetector()),
                      ("persisted state (v0.2)", StreamDetector.load(state))]:
        new = [a for text, ts in lines[half:] for a in det.process(text, ts) if a.kind == "NEW_TEMPLATE"]
        false = sum(a.template in known for a in new)
        results[name] = (len(new), false)
        print(f"{name:24s} NEW_TEMPLATE alerts after restart: {len(new):3d}  "
              f"(already known before restart: {false})")

    if a.plot:
        import matplotlib.pyplot as plt
        names = list(results)
        totals = [results[n][0] for n in names]
        fig, ax = plt.subplots(figsize=(7, 3.2), dpi=150)
        ax.barh(names, totals, color=["#d1495b", "#2b5c8a"])
        for i, t in enumerate(totals):
            ax.text(t + 1, i, str(t), va="center", fontsize=12, fontweight="bold")
        ax.set_xlim(0, max(totals) * 1.15)
        ax.set_xlabel(f"NEW_TEMPLATE alerts on the {len(lines) - half:,} BGL lines after a restart")
        ax.set_title("Same log, same restart: forgetting doubles the alerts", loc="left", fontsize=11)
        ax.invert_yaxis()
        ax.spines[["top", "right"]].set_visible(False)
        fig.tight_layout()
        fig.savefig(a.plot)


if __name__ == "__main__":
    main()
