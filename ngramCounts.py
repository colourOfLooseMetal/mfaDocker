"""
ngramCounts.py — count word n-grams (2–6) across SRT lines that were
successfully aligned by MFA.

"Successfully aligned" means the output_final JSON for that episode contains
at least one aligned word within the line's [xmin, xmax] time window from the
TextGrid. Lines with no matching words in output_final are skipped.

N-grams are built from the TextGrid text (the cleaned SRT lines), strictly
within a single line — no cross-line spanning.

Output: ngrams.txt  — plain text, one n-gram per line, sorted by count desc,
                       grouped by n-gram size. Only phrases with count > MIN_COUNT.
"""

import json
import re
import glob
import os
import bisect
from collections import defaultdict

TEXTGRID_DIR = "Seinfeld/textgrids"
OUTPUT_FINAL  = "output_final"
OUTPUT_TXT    = "ngrams.txt"
MIN_COUNT     = 3
N_MIN, N_MAX  = 2, 6


def parse_textgrid_intervals(path):
    """Return sorted list of (xmin, xmax, text) for non-empty intervals."""
    with open(path, encoding="utf-8") as f:
        content = f.read()
    raw = re.findall(
        r'xmin = ([\d.]+)\s+xmax = ([\d.]+)\s+text = "(.*?)"',
        content,
    )
    return sorted(
        (float(xmin), float(xmax), text.strip())
        for xmin, xmax, text in raw
        if text.strip()
    )


def load_word_starts(json_path):
    """Return sorted list of word start times from output_final JSON."""
    with open(json_path, encoding="utf-8") as f:
        d = json.load(f)
    entries = d["tiers"]["words"]["entries"]
    return sorted(e[0] for e in entries)


def has_aligned_word(word_starts, xmin, xmax):
    """True if any aligned word start falls within [xmin, xmax)."""
    idx = bisect.bisect_left(word_starts, xmin)
    return idx < len(word_starts) and word_starts[idx] < xmax


def tokenize(text):
    """Lowercase, keep contractions, drop punctuation and digits."""
    text = text.lower()
    text = re.sub(u"[‘’“”]", "", text)
    return re.findall(r"[a-z]+(?:'[a-z]+)*", text)


def extract_ngrams(words, n_min, n_max):
    for n in range(n_min, n_max + 1):
        for i in range(len(words) - n + 1):
            yield n, " ".join(words[i : i + n])


def main():
    counts = {n: defaultdict(int) for n in range(N_MIN, N_MAX + 1)}

    tg_files = sorted(glob.glob(os.path.join(TEXTGRID_DIR, "*.TextGrid")))
    print(f"Processing {len(tg_files)} TextGrid files…")

    total_lines = accepted_lines = 0

    for tg_path in tg_files:
        ep_id = os.path.splitext(os.path.basename(tg_path))[0]
        json_path = os.path.join(OUTPUT_FINAL, f"{ep_id}.json")
        if not os.path.exists(json_path):
            print(f"  WARNING: no output_final for {ep_id}, skipping")
            continue

        word_starts = load_word_starts(json_path)
        intervals   = parse_textgrid_intervals(tg_path)

        for xmin, xmax, text in intervals:
            total_lines += 1
            if not has_aligned_word(word_starts, xmin, xmax):
                continue
            words = tokenize(text)
            if len(words) < N_MIN:
                continue
            accepted_lines += 1
            for n, phrase in extract_ngrams(words, N_MIN, N_MAX):
                counts[n][phrase] += 1

    print(f"Lines accepted: {accepted_lines}/{total_lines}")

    with open(OUTPUT_TXT, "w", encoding="utf-8") as out:
        for n in range(N_MIN, N_MAX + 1):
            filtered = sorted(
                ((phrase, cnt) for phrase, cnt in counts[n].items() if cnt > MIN_COUNT),
                key=lambda kv: kv[1],
                reverse=True,
            )
            out.write(f"{'=' * 60}\n")
            out.write(f"  {n}-GRAMS  ({len(filtered)} phrases with count > {MIN_COUNT})\n")
            out.write(f"{'=' * 60}\n")
            for phrase, cnt in filtered:
                out.write(f"{cnt:>6}  {phrase}\n")
            out.write("\n")
            print(f"  {n}-grams: {len(filtered)} phrases")

    print(f"\nWrote {OUTPUT_TXT}")


if __name__ == "__main__":
    main()
