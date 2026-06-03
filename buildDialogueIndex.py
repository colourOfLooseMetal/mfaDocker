"""
buildDialogueIndex.py — build one combined dialogue index from the MFA inputs
and outputs.

The show's dialogue lives in two parallel sets of files:
  - Seinfeld/textgrids/sXXeYY.TextGrid  — MFA input, organized per subtitle
                                          LINE (sanitized text + padded bounds)
  - output_final/sXXeYY.json            — MFA output, organized per WORD
                                          (start, end, word)

~6% of TextGrid lines failed alignment and have no words in output_final.

This script joins the two: it groups each episode's aligned words back into
their source subtitle lines and writes a single file keyed by episode, so
downstream code (sentence boundaries, n-gram cutoffs, ...) reads one source of
truth instead of cross-referencing two files per episode at runtime.

Outputs:
  dialogue.json         — compact, all 171 episodes, only aligned lines.
  dialogue.sample.json  — pretty-printed excerpt (eps 1-5, first 3 lines each)
                          documenting the structure; inspect this instead of
                          opening the large full file.

Per-episode structure:
  {
    "s01e01": {
      "start": 0, "end": 1386.218,
      "lines": [
        { "xmin": 2.1, "xmax": 4.065,
          "text": "Do you know what this is all about? Why we're here?",
          "words": [ {"start": 2.1, "end": 2.16, "word": "do"}, ... ] },
        ...
      ]
    }, ...
  }

Word -> line grouping is by midpoint: each word is assigned to the TextGrid
interval (line) that contains the word's midpoint. Intervals are contiguous and
cover the whole episode timeline, so every word maps to exactly one interval.
Midpoint (not start) is used because TextGrid and JSON times don't match
exactly — rounding + MFA decoding within a threshold — so a line's first word
can start a hair below the line's xmin (float error) and would otherwise be
lost to the preceding silence interval. The midpoint sits firmly inside the
line. Only non-empty intervals that collected >= 1 word are emitted.
"""

import bisect
import glob
import json
import os

from praatio import textgrid

TEXTGRID_DIR = "Seinfeld/textgrids"
OUTPUT_FINAL = "output_final"
OUT_JSON     = "dialogue.json"
OUT_SAMPLE   = "dialogue.sample.json"

SAMPLE_EPISODES = 5   # first N episodes (sorted) in the sample file
SAMPLE_LINES    = 3   # first N aligned lines per sampled episode
ROUND = 3             # time precision in the output


def load_words(json_path):
    """Return words = [(start, end, word), ...] sorted by start."""
    with open(json_path, encoding="utf-8") as f:
        entries = json.load(f)["tiers"]["words"]["entries"]
    return sorted((float(s), float(e), w) for s, e, w in entries)


def episode_lines(tg_path, words):
    """Group aligned words into their TextGrid lines by midpoint. Returns
    (lines, n_nonempty): lines is the list of line dicts for non-empty
    intervals that collected >= 1 word; n_nonempty is the total count of
    non-empty intervals (for drop accounting)."""
    tg = textgrid.openTextgrid(tg_path, includeEmptyIntervals=True)
    intervals = list(tg.getTier(tg.tierNames[0]).entries)

    # Contiguous, sorted intervals: bisect each word's midpoint onto the left
    # edges to find its containing interval.
    edges = [iv.start for iv in intervals]
    buckets = [[] for _ in intervals]
    for s, e, w in words:
        idx = bisect.bisect_right(edges, (s + e) / 2.0) - 1
        if 0 <= idx < len(intervals):
            buckets[idx].append((s, e, w))

    n_nonempty = 0
    lines = []
    for iv, bucket in zip(intervals, buckets):
        text = iv.label.strip()
        if not text:
            continue
        n_nonempty += 1
        if not bucket:
            continue
        lines.append({
            "xmin": round(iv.start, ROUND),
            "xmax": round(iv.end, ROUND),
            "text": text,
            "words": [
                {"start": round(s, ROUND), "end": round(e, ROUND), "word": w}
                for s, e, w in bucket
            ],
        })
    return lines, n_nonempty


def main():
    json_files = sorted(glob.glob(os.path.join(OUTPUT_FINAL, "*.json")))
    print(f"Processing {len(json_files)} episodes…")

    index = {}
    total_lines = total_dropped = total_words = 0

    for json_path in json_files:
        stem = os.path.splitext(os.path.basename(json_path))[0]
        tg_path = os.path.join(TEXTGRID_DIR, f"{stem}.TextGrid")
        if not os.path.exists(tg_path):
            print(f"  WARNING: no TextGrid for {stem}, skipping")
            continue

        with open(json_path, encoding="utf-8") as f:
            meta = json.load(f)
        words = load_words(json_path)
        lines, n_nonempty = episode_lines(tg_path, words)

        dropped = n_nonempty - len(lines)
        kept_words = sum(len(ln["words"]) for ln in lines)

        index[stem] = {
            "start": meta["start"],
            "end": meta["end"],
            "lines": lines,
        }
        total_lines += len(lines)
        total_dropped += dropped
        total_words += kept_words
        print(f"[OK]   {stem}: {len(lines):4} lines  "
              f"{dropped:3} dropped  {kept_words:5} words")

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False)

    # Sample: first SAMPLE_EPISODES episodes, first SAMPLE_LINES lines each.
    sample = {}
    for stem in sorted(index)[:SAMPLE_EPISODES]:
        ep = index[stem]
        sample[stem] = {
            "start": ep["start"],
            "end": ep["end"],
            "lines": ep["lines"][:SAMPLE_LINES],
        }
    with open(OUT_SAMPLE, "w", encoding="utf-8") as f:
        json.dump(sample, f, ensure_ascii=False, indent=2)

    print(f"\nEpisodes: {len(index)}  "
          f"aligned lines: {total_lines}  "
          f"unaligned dropped: {total_dropped}  "
          f"words: {total_words}")
    print(f"Wrote {OUT_JSON} and {OUT_SAMPLE}")


if __name__ == "__main__":
    main()
