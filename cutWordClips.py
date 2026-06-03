"""
Pre-cut every accepted word clip to its own subtitled .mp4 under CLIPS_DIR.

Drives off the tiered candidate index from wordCandidates.py (high-quality pool
plus resurrected low-quality words), extracts each clip from the source .mkv
with the word caption burned in (locked encode params, so the builder can
concat-copy them), and writes a manifest the builder loads instead of touching
the 129 GB of episode .mkv files.

Resumable: existing clip files are skipped. Subtitle is baked in by design.

NOTE: this is the long, storage-heavy step (~47k clips). Tune the cutoff
constants in wordCandidates.py first, then run.
"""

import json
import sys
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from buildNgramVideo import CLIP_H, CLIP_W, MKV_DIR, PAD_SEC, extract_segment
from wordCandidates import build_candidate_index

CLIPS_DIR = r"./wordClips"
SAMPLE_PAD_SEC = 0.05    # padding each side (matches the scoring pad)
WORKERS = 4              # parallel ffmpeg processes
MAX_WORDS = None         # cap words for a test run (None = all)

_print_lock = threading.Lock()


def clip_relpath(word, occ):
    name = (f"{occ['stem']}_{int(occ['start'] * 1000)}"
            f"_c{occ['conf']:.2f}_{occ['quality']}.mp4")
    return f"{word}/{name}"


def cut_one(word, occ, clips_root, mkv_dir):
    """Extract one subtitled clip (skip if present). Returns a manifest entry."""
    rel = clip_relpath(word, occ)
    out = clips_root / rel
    entry = {
        "file": rel, "stem": occ["stem"], "start": occ["start"],
        "end": occ["end"], "conf": occ["conf"], "quality": occ["quality"],
    }
    if out.exists():
        return word, entry, "skip"
    out.parent.mkdir(parents=True, exist_ok=True)
    mkv = Path(mkv_dir) / f"{occ['stem']}.mkv"
    try:
        extract_segment(
            mkv, max(0.0, occ["start"] - SAMPLE_PAD_SEC),
            occ["end"] + SAMPLE_PAD_SEC, out, caption=word, pad_after=0.0,
            scale_to=(CLIP_W, CLIP_H))
    finally:
        Path(str(out) + ".caption.txt").unlink(missing_ok=True)
    return word, entry, "cut"


def main():
    index, words = build_candidate_index()
    if MAX_WORDS is not None:
        words = words[:MAX_WORDS]

    clips_root = Path(CLIPS_DIR)
    clips_root.mkdir(parents=True, exist_ok=True)

    tasks = [(w, occ) for w in words for occ in index[w]]
    total = len(tasks)
    print(f"{len(words)} words, {total} clips to ensure "
          f"({WORKERS} workers) -> {CLIPS_DIR}")

    manifest = defaultdict(list)
    done = cut = skip = fail = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(cut_one, w, occ, clips_root, MKV_DIR): (w, occ)
                for w, occ in tasks}
        for fut in as_completed(futs):
            w, occ = futs[fut]
            done += 1
            try:
                word, entry, status = fut.result()
                manifest[word].append(entry)
                cut += status == "cut"
                skip += status == "skip"
            except Exception as e:
                fail += 1
                with _print_lock:
                    print(f"[FAIL] {w} {occ['stem']}@{occ['start']}: {e}",
                          file=sys.stderr)
            if done % 500 == 0 or done == total:
                with _print_lock:
                    print(f"  {done}/{total}  cut={cut} skip={skip} fail={fail}")

    # Stable manifest: words sorted, clips highest-conf first.
    ordered = {w: sorted(manifest[w], key=lambda e: -e["conf"])
               for w in sorted(manifest)}
    with open(clips_root / "index.json", "w", encoding="utf-8") as f:
        json.dump(ordered, f, ensure_ascii=False)

    print(f"\nDone. {len(ordered)} words, {cut} cut, {skip} skipped, "
          f"{fail} failed. Manifest -> {CLIPS_DIR}/index.json")


if __name__ == "__main__":
    main()
