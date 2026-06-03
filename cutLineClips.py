"""
Cut every subtitle line that contains a candidate n-gram to its own .mp4.

The n-gram analogue of cutWordClips.py: drives off the candidate pool from
ngramCandidates.py, cuts each *line* that holds >=1 surviving n-gram from the
source .mkv (normalized 720x540, locked encode params so sub-segments can be
concat-copied later), and writes an inverted index mapping each n-gram text to
its occurrences -- with the line clip file and the n-gram's start/end *inside
that clip*. A future sentence-builder extracts and stitches those sub-segments.

Lines with no surviving candidate are never cut. Captions are word-level
karaoke: each word is burned in only during its own MFA interval, so any n-gram
sub-segment shows exactly the words spoken then -- never the full line.

Resumable: existing clip files are skipped.

NOTE: this is the storage-heavy step -- with unigrams + resurrection nearly
every line survives, so expect tens of thousands of line clips. Tune the cutoff
constants in ngramCandidates.py first, then run.
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from buildNgramVideo import (
    CAPTION_BORDER, CAPTION_BOTTOM_PAD, CAPTION_FONT, CAPTION_FONTSIZE,
    CLIP_H, CLIP_W, MKV_DIR, PAD_SEC, TARGET_AR, TARGET_CRF, TARGET_FPS,
    _ffmpeg_path_escape,
)
from ngramCandidates import NGRAMSCORES_DIR, build_ngram_candidate_index

CLIPS_DIR = r"./lineClips"
WORKERS = 4              # parallel ffmpeg processes
MAX_LINES = None        # cap lines for a test run (None = all)
VERBOSE_INDEX = False    # True: include stem/line_idx/n/i/conf/quality in index
                         # False: slim format {file, start_in_clip, end_in_clip}

_print_lock = threading.Lock()


def clip_relpath(global_idx):
    return f"sfvL_{global_idx}.mp4"


def line_word_timings(ngramscores_dir, needed):
    """Return {(stem, line_idx): [{"text","start","end"}, ...]} for the lines
    we will cut. Word timings come from each line's n=1 ngrams (ordered by i).

    `needed`: set of (stem, line_idx). Reads each needed episode JSON once.
    """
    by_stem = defaultdict(set)
    for stem, line_idx in needed:
        by_stem[stem].add(line_idx)

    timings = {}
    for stem, idxs in by_stem.items():
        path = Path(ngramscores_dir) / f"{stem}.json"
        for line in json.load(open(path, encoding="utf-8"))["lines"]:
            if line["line_idx"] not in idxs:
                continue
            words = sorted((g for g in line["ngrams"] if g["n"] == 1),
                           key=lambda g: g["i"])
            timings[(stem, line["line_idx"])] = [
                {"text": w["text"], "start": w["start"], "end": w["end"]}
                for w in words
            ]
    return timings


def extract_line_with_word_captions(mkv_path, clip_start, clip_end, out_path,
                                    word_timings, scale_to=(CLIP_W, CLIP_H)):
    """Cut [clip_start, clip_end] from mkv, burning each word in only during its
    own interval (karaoke). Mirrors buildWordVideo.extract_segment's no-pad path
    (fast -ss seek, locked encode params, -avoid_negative_ts make_zero so output
    PTS start at 0 and the between(t,...) windows line up).
    """
    duration = max(clip_end - clip_start, 0.01)

    # One temp textfile per word (apostrophe-safe, like extract_segment), each
    # drawn only while between(t, word_start, word_end) relative to clip start.
    cap_paths = []
    drawtext_filters = []
    try:
        for w in word_timings:
            fd, cap = tempfile.mkstemp(prefix="cap_", suffix=".txt")
            os.close(fd)
            cap_path = Path(cap)
            cap_path.write_text(w["text"], encoding="utf-8")
            cap_paths.append(cap_path)
            a = max(0.0, w["start"] - clip_start)
            b = max(a, w["end"] - clip_start)
            drawtext_filters.append(
                f"drawtext=textfile='{_ffmpeg_path_escape(cap_path)}'"
                f":fontfile='{_ffmpeg_path_escape(CAPTION_FONT)}'"
                f":fontsize={CAPTION_FONTSIZE}"
                f":fontcolor=white"
                f":borderw={CAPTION_BORDER}"
                f":bordercolor=black"
                f":x=(w-text_w)/2"
                f":y=h-text_h-{CAPTION_BOTTOM_PAD}"
                f":enable='between(t,{a:.3f},{b:.3f})'"
            )

        vf_parts = [f"fps={TARGET_FPS}", "setsar=1", "format=yuv420p"]
        if scale_to:
            sw, sh = scale_to
            vf_parts += [
                f"scale={sw}:{sh}:force_original_aspect_ratio=increase",
                f"crop={sw}:{sh}", "setsar=1",
            ]
        vf_parts += drawtext_filters
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{clip_start:.3f}",
            "-i", str(mkv_path),
            "-t", f"{duration:.3f}",
            "-map", "0:v:0", "-map", "0:a:0",
            "-vf", ",".join(vf_parts),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", str(TARGET_CRF),
            "-c:a", "aac", "-b:a", "192k", "-ar", str(TARGET_AR), "-ac", "2",
            "-avoid_negative_ts", "make_zero",
            str(out_path),
        ]
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace")
            raise subprocess.CalledProcessError(
                result.returncode, cmd, output=result.stdout,
                stderr=stderr.encode())
    finally:
        for cap_path in cap_paths:
            cap_path.unlink(missing_ok=True)


def cut_one(global_idx, stem, line_idx, xmin, xmax, words, clips_root, mkv_dir):
    """Cut one line clip (skip if present). Returns (clip_start, status)."""
    clip_start = max(0.0, xmin - PAD_SEC)
    clip_end = xmax + PAD_SEC
    out = clips_root / clip_relpath(global_idx)
    if out.exists():
        return clip_start, "skip"
    mkv = Path(mkv_dir) / f"{stem}.mkv"
    extract_line_with_word_captions(mkv, clip_start, clip_end, out, words)
    return clip_start, "cut"


def main():
    index, ngrams = build_ngram_candidate_index()

    # Unique lines to cut: (stem, line_idx) -> (xmin, xmax).
    line_bounds = {}
    for occs in index.values():
        for o in occs:
            line_bounds[(o["stem"], o["line_idx"])] = (o["xmin"], o["xmax"])

    needed = sorted(line_bounds)
    if MAX_LINES is not None:
        needed = needed[:MAX_LINES]
        keep = set(needed)
        line_bounds = {k: v for k, v in line_bounds.items() if k in keep}

    timings = line_word_timings(NGRAMSCORES_DIR, set(needed))

    clips_root = Path(CLIPS_DIR)
    clips_root.mkdir(parents=True, exist_ok=True)

    total = len(needed)
    print(f"{len(ngrams)} n-grams, {total} lines to cut "
          f"({WORKERS} workers) -> {CLIPS_DIR}")

    # Assign each (stem, line_idx) a stable global index (sorted order).
    global_indices = {key: i for i, key in enumerate(needed)}

    clip_starts = {}
    done = cut = skip = fail = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {}
        for stem, line_idx in needed:
            xmin, xmax = line_bounds[(stem, line_idx)]
            words = timings.get((stem, line_idx), [])
            gidx = global_indices[(stem, line_idx)]
            futs[ex.submit(cut_one, gidx, stem, line_idx, xmin, xmax, words,
                           clips_root, MKV_DIR)] = (stem, line_idx)
        for fut in as_completed(futs):
            stem, line_idx = futs[fut]
            done += 1
            try:
                clip_start, status = fut.result()
                clip_starts[(stem, line_idx)] = clip_start
                cut += status == "cut"
                skip += status == "skip"
            except Exception as e:
                fail += 1
                with _print_lock:
                    print(f"[FAIL] {stem} L{line_idx}: {e}", file=sys.stderr)
            if done % 200 == 0 or done == total:
                with _print_lock:
                    print(f"  {done}/{total}  cut={cut} skip={skip} fail={fail}")

    # Inverted index: n-gram text -> occurrences with clip file + in-clip times.
    # Occurrences are sorted highest-conf first (from ngramCandidates).
    # VERBOSE_INDEX=False: slim {file, start_in_clip, end_in_clip} only.
    # VERBOSE_INDEX=True:  full {file, stem, line_idx, n, i, conf, quality, ...}.
    manifest = {}
    for text in sorted(index):
        rows = []
        for o in index[text]:
            key = (o["stem"], o["line_idx"])
            if key not in clip_starts:
                continue  # line not cut (MAX_LINES cap or failure)
            cs = clip_starts[key]
            gidx = global_indices[key]
            if VERBOSE_INDEX:
                rows.append({
                    "file": clip_relpath(gidx),
                    "stem": o["stem"], "line_idx": o["line_idx"],
                    "n": o["n"], "i": o["i"],
                    "conf": o["conf"], "quality": o["quality"],
                    "start_in_clip": round(o["start"] - cs, 3),
                    "end_in_clip": round(o["end"] - cs, 3),
                })
            else:
                rows.append({
                    "file": clip_relpath(gidx),
                    "start_in_clip": round(o["start"] - cs, 3),
                    "end_in_clip": round(o["end"] - cs, 3),
                })
        if rows:
            manifest[text] = rows

    with open(clips_root / "index.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False)

    print(f"\nDone. {len(manifest)} n-grams, {cut} cut, {skip} skipped, "
          f"{fail} failed. Manifest -> {CLIPS_DIR}/index.json")


if __name__ == "__main__":
    main()
