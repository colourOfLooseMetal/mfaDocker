"""
Render subtitled word clips grouped by confidence, to evaluate the wav2vec2
score two ways:

SET 1 — relative deciles (frequent words, >= FREQ_MIN occurrences):
  For each frequent word, rank its own occurrences by conf and split into 10
  deciles. Folder d01 = each word's top-10% instances, d10 = bottom-10%.
  Tests whether "top N% of a word's own occurrences" picks good instances.

SET 2 — absolute bands (rare words, RARE_MIN..RARE_MAX occurrences):
  Bucket rare-word occurrences by absolute conf (0.0-0.1 .. 0.9-1.0).
  Tests whether raw conf tracks quality when a word is too rare to rank.

Reads wordScores/*.json, renders from Seinfeld/allEpisodes/*.mkv via the
reused extract_segment from buildWordVideo.py. Conf is encoded in each
filename for at-a-glance review.
"""

import csv
import glob
import json
import random
from collections import defaultdict
from pathlib import Path

from buildNgramVideo import extract_segment, sanitize_filename, unique_output_path

WORDSCORES_DIR = r"./wordScores"
MKV_DIR = r"./Seinfeld/allEpisodes"
OUT_DIR = r"./wordVideos/confSamples"

PER_FOLDER = 20          # clips per folder
MAX_PER_WORD = 2         # cap occurrences of the same word within one folder
FREQ_MIN = 30            # >= this many occurrences => "frequent" (Sets 1 & 3)
RARE_MIN, RARE_MAX = 1, 8  # occurrence range for "rare" (Set 2, incl. singletons)
PAD_SEC = 0.05           # clip padding each side (matches the scoring pad)
SEED = 0

# Set 3 — frequent words, fine absolute bands over a zoomed conf range:
FREQ_ABS_LO, FREQ_ABS_HI = 0.0, 0.2

# Which sets to render. Gated so re-runs don't double up already-reviewed
# folders (unique_output_path would suffix rather than overwrite).
# Options: "relative", "absolute_rare", "frequent_absolute".
RENDER_SETS = {"frequent_absolute"}


def load_occurrences():
    """word -> list of dicts {word, stem, start, end, conf}."""
    by_word = defaultdict(list)
    for p in glob.glob(f"{WORDSCORES_DIR}/*.json"):
        stem = Path(p).stem
        for r in json.load(open(p, encoding="utf-8"))["words"]:
            if r["conf"] is None:
                continue
            by_word[r["word"]].append({
                "word": r["word"], "stem": stem,
                "start": r["start"], "end": r["end"], "conf": r["conf"],
            })
    return by_word


def take(pool, rng):
    """Sample up to PER_FOLDER from pool, capping repeats per word."""
    rng.shuffle(pool)
    picked, per_word = [], defaultdict(int)
    for occ in pool:
        if per_word[occ["word"]] >= MAX_PER_WORD:
            continue
        picked.append(occ)
        per_word[occ["word"]] += 1
        if len(picked) >= PER_FOLDER:
            break
    # Backfill ignoring the cap if the pool was too small to reach PER_FOLDER.
    if len(picked) < PER_FOLDER:
        chosen = {id(o) for o in picked}
        for occ in pool:
            if id(occ) not in chosen:
                picked.append(occ)
                if len(picked) >= PER_FOLDER:
                    break
    return picked


def build_relative_pools(by_word):
    """Decile pools (0=top conf .. 9=bottom) across frequent words."""
    pools = [[] for _ in range(10)]
    for word, occs in by_word.items():
        if len(occs) < FREQ_MIN:
            continue
        ordered = sorted(occs, key=lambda o: o["conf"], reverse=True)
        n = len(ordered)
        for i, occ in enumerate(ordered):
            pools[i * 10 // n].append(occ)
    return pools


def build_absolute_pools(by_word):
    """Absolute conf-band pools (0=[0,0.1) .. 9=[0.9,1.0]) for rare words."""
    pools = [[] for _ in range(10)]
    for word, occs in by_word.items():
        if not (RARE_MIN <= len(occs) <= RARE_MAX):
            continue
        for occ in occs:
            pools[min(9, int(occ["conf"] * 10))].append(occ)
    return pools


def build_frequent_absolute_pools(by_word, lo, hi, nbands=10):
    """Fine absolute-conf bands over [lo, hi) for frequent words only."""
    width = (hi - lo) / nbands
    pools = [[] for _ in range(nbands)]
    for word, occs in by_word.items():
        if len(occs) < FREQ_MIN:
            continue
        for occ in occs:
            c = occ["conf"]
            if lo <= c < hi:
                pools[min(nbands - 1, int((c - lo) / width))].append(occ)
    return pools


def render_folder(occs, folder, manifest, set_name, bucket):
    folder.mkdir(parents=True, exist_ok=True)
    for occ in occs:
        mkv = Path(MKV_DIR) / f"{occ['stem']}.mkv"
        if not mkv.exists():
            print(f"[WARN] missing mkv {occ['stem']}")
            continue
        base = sanitize_filename(
            [occ["word"], occ["stem"], str(int(occ["start"] * 1000)),
             f"c{occ['conf']:.2f}"])
        out = unique_output_path(folder, base)
        try:
            extract_segment(
                mkv, max(0.0, occ["start"] - PAD_SEC), occ["end"] + PAD_SEC,
                out, caption=occ["word"], pad_after=0.0)
        except Exception as e:
            print(f"[FAIL] {occ['word']} {occ['stem']}: {e}")
            continue
        finally:
            # extract_segment writes a drawtext sidecar next to the clip; the
            # render is done, so drop it to keep the review folders clean.
            Path(str(out) + ".caption.txt").unlink(missing_ok=True)
        manifest.append({
            "set": set_name, "bucket": bucket, "word": occ["word"],
            "stem": occ["stem"], "start": occ["start"], "end": occ["end"],
            "conf": occ["conf"], "file": str(out.relative_to(Path(OUT_DIR))),
        })
    print(f"[OK]   {set_name}/{folder.name}: {len(occs)} clip(s)")


def main():
    rng = random.Random(SEED)
    by_word = load_occurrences()
    print(f"Loaded {sum(len(v) for v in by_word.values())} occurrences, "
          f"{len(by_word)} unique words.\n")

    out_root = Path(OUT_DIR)
    manifest = []

    # SET 1 — relative deciles, frequent words.
    if "relative" in RENDER_SETS:
        rel = build_relative_pools(by_word)
        rel_root = out_root / "relative_frequent"
        labels = ["top10pct", "10-20pct", "20-30pct", "30-40pct", "40-50pct",
                  "50-60pct", "60-70pct", "70-80pct", "80-90pct", "bottom10pct"]
        print(f"SET 1 relative deciles (words with >= {FREQ_MIN} occ):")
        for d in range(10):
            folder = rel_root / f"d{d + 1:02d}_{labels[d]}"
            render_folder(take(rel[d], rng), folder, manifest,
                          "relative", labels[d])

    # SET 2 — absolute bands, rare words.
    if "absolute_rare" in RENDER_SETS:
        ab = build_absolute_pools(by_word)
        ab_root = out_root / "absolute_rare"
        print(f"\nSET 2 absolute bands (words with {RARE_MIN}-{RARE_MAX} occ):")
        for b in range(10):
            lo, hi = b / 10, (b + 1) / 10
            folder = ab_root / f"b{b + 1:02d}_{lo:.1f}-{hi:.1f}"
            render_folder(take(ab[b], rng), folder, manifest,
                          "absolute", f"{lo:.1f}-{hi:.1f}")

    # SET 3 — fine absolute bands over [LO,HI), frequent words.
    if "frequent_absolute" in RENDER_SETS:
        fa = build_frequent_absolute_pools(by_word, FREQ_ABS_LO, FREQ_ABS_HI)
        fa_root = out_root / "frequent_absolute"
        width = (FREQ_ABS_HI - FREQ_ABS_LO) / 10
        print(f"\nSET 3 fine absolute bands {FREQ_ABS_LO}-{FREQ_ABS_HI} "
              f"(words with >= {FREQ_MIN} occ):")
        for b in range(10):
            lo = FREQ_ABS_LO + b * width
            hi = lo + width
            folder = fa_root / f"fb{b + 1:02d}_{lo:.2f}-{hi:.2f}"
            render_folder(take(fa[b], rng), folder, manifest,
                          "frequent_absolute", f"{lo:.2f}-{hi:.2f}")

    # Append to the manifest rather than clobber prior sets' rows.
    man_path = out_root / "manifest.csv"
    fieldnames = ["set", "bucket", "word", "stem", "start", "end", "conf", "file"]
    write_header = not man_path.exists()
    with open(man_path, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            w.writeheader()
        w.writerows(manifest)

    print(f"\nDone. {len(manifest)} clips in {OUT_DIR}/  |  manifest.csv updated.")


if __name__ == "__main__":
    main()
