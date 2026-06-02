"""
Confidence distribution chart for the wav2vec2 word scores.

Reads wordScores/*.json and writes, into wordVideos/confSamples/:
  conf_distribution.png  — two panels: full 0-1.0 (log y) and a 0-0.2 zoom
  conf_distribution.csv  — bin_lo, bin_hi, count  (0.01-wide bins)
"""

import csv
import glob
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

WORDSCORES_DIR = r"./wordScores"
OUT_DIR = r"./wordVideos/confSamples"


def main():
    confs = []
    for p in glob.glob(f"{WORDSCORES_DIR}/*.json"):
        for r in json.load(open(p, encoding="utf-8"))["words"]:
            if r["conf"] is not None:
                confs.append(r["conf"])
    confs = np.array(confs)
    n = len(confs)
    print(f"{n} scored occurrences; mean={confs.mean():.3f} "
          f"median={np.median(confs):.3f}")

    out = Path(OUT_DIR)
    out.mkdir(parents=True, exist_ok=True)

    # CSV at 0.01 resolution.
    edges = np.arange(0, 1.0001, 0.01)
    hist, _ = np.histogram(confs, bins=edges)
    with open(out / "conf_distribution.csv", "w", encoding="utf-8",
              newline="") as f:
        w = csv.writer(f)
        w.writerow(["bin_lo", "bin_hi", "count", "pct_of_total"])
        for i, c in enumerate(hist):
            w.writerow([f"{edges[i]:.2f}", f"{edges[i + 1]:.2f}", int(c),
                        f"{100 * c / n:.2f}"])

    # Chart.
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 8))

    ax1.hist(confs, bins=np.arange(0, 1.02, 0.02), color="#3477eb",
             edgecolor="white", linewidth=0.3)
    ax1.set_yscale("log")
    ax1.set_title(f"Word confidence distribution — all {n:,} occurrences "
                  f"(log scale, 0.02 bins)")
    ax1.set_xlabel("wav2vec2 CTC confidence")
    ax1.set_ylabel("word count (log)")
    ax1.set_xlim(0, 1)
    ax1.grid(axis="y", alpha=0.3)

    zoom = confs[confs < 0.2]
    ax2.hist(zoom, bins=np.arange(0, 0.201, 0.01), color="#eb7d34",
             edgecolor="white", linewidth=0.3)
    ax2.set_title(f"Zoom 0.0-0.2 ({len(zoom):,} occ, "
                  f"{100 * len(zoom) / n:.0f}% of corpus; 0.01 bins)")
    ax2.set_xlabel("wav2vec2 CTC confidence")
    ax2.set_ylabel("word count")
    ax2.set_xlim(0, 0.2)
    ax2.grid(axis="y", alpha=0.3)

    fig.tight_layout()
    fig.savefig(out / "conf_distribution.png", dpi=130)
    print(f"Wrote {out / 'conf_distribution.png'} and conf_distribution.csv")


if __name__ == "__main__":
    main()
