"""
Triage MFA alignment quality and render single-word inspection clips.

Reads `alignment_analysis.csv` (one row per aligned line/utterance), classifies
each line by the documented quality rules, persists the per-line verdict, then
samples random subtitled single-word clips from each category so the numbers
can be calibrated by ear.

Categories (first matching rule wins; thresholds are corpus percentiles):
  unscored          — line had empty metric fields (too short to score)
  likely_failure    — speech_ll low  AND phone_dur_dev high
  noisy_audio       — speech_ll low  AND dev normal AND snr low
  stylistic_outlier — speech_ll low  AND dev normal AND snr high
  duration_anomaly  — speech_ll normal AND phone_dur_dev high
  ok                — everything else

Reuses extract_segment / sanitize_filename / unique_output_path / PAD_SEC from
buildWordVideo.py (its tkinter UI is guarded under __main__, so importing is
side-effect free).
"""

import csv
import json
import random
import sys
from pathlib import Path

import numpy as np

from buildWordVideo import (
    PAD_SEC,
    extract_segment,
    sanitize_filename,
    unique_output_path,
)

JSON_DIR = r"./output_final"
MKV_DIR = r"./Seinfeld/allEpisodes"
CSV_PATH = r"./output_final/alignment_analysis.csv"
OUTPUT_DIR = r"./wordVideos/quality_samples"
LINE_QUALITY_CSV = r"./output_final/alignment_line_quality.csv"

SAMPLES_PER_CATEGORY = 20
MIN_WORD_DUR = 0.12      # skip ultra-short words so clips are watchable
MATCH_TOL = 0.05         # rounding tolerance for word -> line containment
SEED = None              # set an int for reproducible sampling

SLL_LOW_PCT, PDD_HIGH_PCT, SNR_LOW_PCT = 10, 90, 25

METRIC_COLS = [
    "overall_log_likelihood",
    "speech_log_likelihood",
    "phone_duration_deviation",
    "snr",
]

CATEGORIES = [
    "likely_failure",
    "duration_anomaly",
    "stylistic_outlier",
    "noisy_audio",
    "ok",
    "unscored",
]

PRIORITY = {
    "likely_failure": "high",
    "duration_anomaly": "high",
    "stylistic_outlier": "medium",
    "noisy_audio": "low",
    "ok": "none",
    "unscored": "none",
}


# --- Parsing & classification -------------------------------------------------

def _num(s):
    s = (s or "").strip()
    return float(s) if s else None


def load_lines(csv_path):
    """Read the analysis CSV into line dicts; empty metrics become None."""
    rows = []
    with open(csv_path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append({
                "file": r["file"],
                "begin": float(r["begin"]),
                "end": float(r["end"]),
                "overall_ll": _num(r["overall_log_likelihood"]),
                "speech_ll": _num(r["speech_log_likelihood"]),
                "phone_dur_dev": _num(r["phone_duration_deviation"]),
                "snr": _num(r["snr"]),
            })
    return rows


def compute_thresholds(rows):
    def col(key):
        return np.array([r[key] for r in rows if r[key] is not None])

    sll_low = float(np.percentile(col("speech_ll"), SLL_LOW_PCT))
    pdd_high = float(np.percentile(col("phone_dur_dev"), PDD_HIGH_PCT))
    snr_low = float(np.percentile(col("snr"), SNR_LOW_PCT))
    return sll_low, pdd_high, snr_low


def classify(row, sll_low, pdd_high, snr_low):
    sll, pdd, snr = row["speech_ll"], row["phone_dur_dev"], row["snr"]
    if sll is None or pdd is None or snr is None:
        return "unscored"
    sll_is_low = sll <= sll_low
    pdd_is_high = pdd >= pdd_high
    snr_is_low = snr <= snr_low
    if sll_is_low and pdd_is_high:
        return "likely_failure"
    if sll_is_low and not pdd_is_high and snr_is_low:
        return "noisy_audio"
    if sll_is_low and not pdd_is_high and not snr_is_low:
        return "stylistic_outlier"
    if not sll_is_low and pdd_is_high:
        return "duration_anomaly"
    return "ok"


def write_line_quality(rows, out_path):
    fieldnames = ["file", "begin", "end", *METRIC_COLS, "category", "priority"]
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({
                "file": r["file"],
                "begin": r["begin"],
                "end": r["end"],
                "overall_log_likelihood": r["overall_ll"],
                "speech_log_likelihood": r["speech_ll"],
                "phone_duration_deviation": r["phone_dur_dev"],
                "snr": r["snr"],
                "category": r["category"],
                "priority": PRIORITY[r["category"]],
            })


# --- Word lookup --------------------------------------------------------------

_word_cache = {}


def words_for_stem(stem, json_dir):
    """Return cached `[start, end, word]` entries for an episode, or []."""
    if stem in _word_cache:
        return _word_cache[stem]
    path = Path(json_dir) / f"{stem}.json"
    entries = []
    try:
        with open(path, encoding="utf-8") as f:
            entries = json.load(f)["tiers"]["words"]["entries"]
    except (OSError, KeyError, json.JSONDecodeError) as e:
        print(f"[WARN] could not read words for {stem}: {e}", file=sys.stderr)
    _word_cache[stem] = entries
    return entries


def eligible_words_in_line(line, json_dir):
    """Words whose midpoint falls within the line bounds, filtered."""
    lo, hi = line["begin"] - MATCH_TOL, line["end"] + MATCH_TOL
    out = []
    for start, end, word in words_for_stem(line["file"], json_dir):
        mid = (start + end) / 2.0
        if not (lo <= mid <= hi):
            continue
        w = (word or "").lower().strip()
        if not w or (w.startswith("<") and w.endswith(">")):
            continue
        if end - start < MIN_WORD_DUR:
            continue
        out.append((start, end, w))
    return out


# --- Sampling & rendering -----------------------------------------------------

def sample_and_render(by_category, json_dir, mkv_dir, output_dir, manifest):
    out_root = Path(output_dir)
    rng = random.Random(SEED)
    rendered = {}

    for category in CATEGORIES:
        lines = by_category.get(category, [])
        rng.shuffle(lines)
        cat_dir = out_root / category
        count = 0

        for line in lines:
            if count >= SAMPLES_PER_CATEGORY:
                break
            choices = eligible_words_in_line(line, json_dir)
            if not choices:
                continue
            start, end, word = rng.choice(choices)
            stem = line["file"]
            mkv = Path(mkv_dir) / f"{stem}.mkv"
            if not mkv.exists():
                print(f"[WARN] missing mkv for {stem}", file=sys.stderr)
                continue

            base = sanitize_filename([word, stem, str(int(start * 1000))])
            out_path = unique_output_path(cat_dir, base)
            try:
                extract_segment(
                    mkv, max(0.0, start - PAD_SEC), end + PAD_SEC, out_path,
                    caption=word, pad_after=0.0,
                )
            except Exception as e:
                print(f"[FAIL] {category} {word} ({stem}): {e}", file=sys.stderr)
                continue

            manifest.append({
                "category": category,
                "priority": PRIORITY[category],
                "word": word,
                "stem": stem,
                "start": round(start, 3),
                "end": round(end, 3),
                "word_dur": round(end - start, 3),
                "line_begin": line["begin"],
                "line_end": line["end"],
                "speech_ll": line["speech_ll"],
                "phone_dur_dev": line["phone_dur_dev"],
                "snr": line["snr"],
                "file": str(out_path.relative_to(out_root)),
            })
            count += 1
            print(f"[OK]   {category:18} {word:20} {stem} @ {start:.2f}s")

        rendered[category] = count

    return rendered


def write_manifest(manifest, output_dir):
    if not manifest:
        return
    path = Path(output_dir) / "manifest.csv"
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(manifest[0].keys()))
        w.writeheader()
        w.writerows(manifest)


# --- Main ---------------------------------------------------------------------

def main():
    rows = load_lines(CSV_PATH)
    print(f"Loaded {len(rows)} lines from {CSV_PATH}")

    sll_low, pdd_high, snr_low = compute_thresholds(rows)
    print(f"Thresholds: speech_ll low <= {sll_low:.2f}, "
          f"phone_dur_dev high >= {pdd_high:.2f}, snr low <= {snr_low:.2f}\n")

    by_category = {c: [] for c in CATEGORIES}
    for r in rows:
        r["category"] = classify(r, sll_low, pdd_high, snr_low)
        by_category[r["category"]].append(r)

    print("Category tally:")
    for c in CATEGORIES:
        print(f"  {c:18} {len(by_category[c]):6}")
    print()

    write_line_quality(rows, LINE_QUALITY_CSV)
    print(f"Wrote per-line verdicts -> {LINE_QUALITY_CSV}\n")

    manifest = []
    rendered = sample_and_render(
        by_category, JSON_DIR, MKV_DIR, OUTPUT_DIR, manifest)
    write_manifest(manifest, OUTPUT_DIR)

    print("\nRendered per category:")
    for c in CATEGORIES:
        print(f"  {c:18} {rendered.get(c, 0):3} clip(s)")
    print(f"\nClips in {OUTPUT_DIR}/<category>/  |  manifest.csv written.")


if __name__ == "__main__":
    main()
