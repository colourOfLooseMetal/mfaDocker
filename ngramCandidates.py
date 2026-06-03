"""
Confidence-aware n-gram-candidate index.

The n-gram analogue of wordCandidates.py: reads per-n-gram wav2vec2
confidences from ngramScores/*.json and applies a quality-first cutoff to
produce, per distinct n-gram text, a small pool of the best occurrences to use
when building sentences. Importable by cutLineClips.py; run directly to print
cutoff stats or dump the index.

Grouping is by the n-gram `text` (lowercase, space-joined). Because every
occurrence of a given text shares the same length, grouping by text implicitly
groups by n -- so the cutoff floors are keyed by n: confidence scale grows with
n (n=1 mean ~0.11 -> n=6 mean ~0.45), and a single global floor would over-keep
long phrases while gutting single words.

Cutoff per n-gram (mirrors wordCandidates.keep_candidates):
  floor = FLOORS[n] * RARE_SCALE if total occ <= RARE_OCC_MAX else FLOORS[n]
  survivors = occurrences with conf >= floor, sorted by conf desc
  target = min(MAX_PER_NGRAM, max(MIN_KEEP, ceil(TARGET_FRAC * n_survivors)))
  walk survivors high->low, keeping until `target`, but stop early once a clip
  is below GOOD[n] and we already hold MIN_KEEP.

TUNE FLOORS / GOOD after the full 171-episode scoring run, like cutWordClips.py
asks for the word path. The defaults below scale the word thresholds upward
with n from the observed per-n conf distributions.
"""

import glob
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

NGRAMSCORES_DIR = r"./ngramScores"
MAX_STEMS = None         # cap episodes for a test run (None = all)

# Per-n quality floors (TUNE). n=1 reuses wordCandidates' ABS_FLOOR/GOOD.
FLOORS = {1: 0.12, 2: 0.18, 3: 0.24, 4: 0.30, 5: 0.36, 6: 0.42}
GOOD = {1: 0.20, 2: 0.27, 3: 0.34, 4: 0.41, 5: 0.48, 6: 0.55}
RARE_SCALE = 0.4       # rare n-grams (<= RARE_OCC_MAX occ) get a softer floor
RARE_OCC_MAX = 3
MIN_KEEP = 3           # keep at least this many when available, even if < GOOD
MAX_PER_NGRAM = 20     # hard cap on pool size
TARGET_FRAC = 0.50     # ~half retention target (capped by MAX_PER_NGRAM)

# Low-quality resurrection: ONLY for n-grams with no clip clearing the floor, so
# a phrase needed for a sentence doesn't vanish. Flagged quality="low".
LOW_FLOOR = 0.02       # drop truly-broken clips (~0 conf misalignments)
LOW_MAX = 5            # best N clips to resurrect for an otherwise-lost n-gram


def keep_candidates(occs):
    """Apply the per-n quality-first cutoff to one n-gram's occurrences.

    `occs`: list of dicts with at least "conf" and "n" (all share the same n).
    Returns the kept sublist (highest-conf first). Empty if nothing clears the
    floor.
    """
    n = occs[0]["n"]
    floor = FLOORS[n] * RARE_SCALE if len(occs) <= RARE_OCC_MAX else FLOORS[n]
    good = GOOD[n]
    survivors = sorted((o for o in occs if o["conf"] >= floor),
                       key=lambda o: o["conf"], reverse=True)
    n_surv = len(survivors)
    if n_surv == 0:
        return []
    target = min(MAX_PER_NGRAM, max(MIN_KEEP, math.ceil(TARGET_FRAC * n_surv)))
    keep = []
    for o in survivors:
        if len(keep) >= target:
            break
        if o["conf"] < good and len(keep) >= MIN_KEEP:
            break
        keep.append(o)
    return keep


def build_ngram_candidate_index(ngramscores_dir=NGRAMSCORES_DIR):
    """Return (index, ngrams).

    index = {text: [{"stem","line_idx","n","i","start","end","conf",
                     "xmin","xmax","quality"}, ...]}  (post-cutoff pool)
    ngrams = sorted list of usable n-gram texts.
    """
    by_text = defaultdict(list)
    paths = sorted(glob.glob(f"{ngramscores_dir}/*.json"))
    if MAX_STEMS is not None:
        paths = paths[:MAX_STEMS]
    for p in paths:
        stem = Path(p).stem
        for line in json.load(open(p, encoding="utf-8"))["lines"]:
            xmin, xmax = line["xmin"], line["xmax"]
            line_idx = line["line_idx"]
            for g in line["ngrams"]:
                if g["conf"] is None:
                    continue  # too_short / no_target_tokens
                text = g["text"]
                # Drop empties and non-lexical fragments (mirrors wordCandidates).
                if not text or not text[0].isalpha():
                    continue
                by_text[text].append({
                    "stem": stem, "line_idx": line_idx,
                    "n": g["n"], "i": g["i"],
                    "start": g["start"], "end": g["end"], "conf": g["conf"],
                    "xmin": xmin, "xmax": xmax,
                })

    index = {}
    for text, occs in by_text.items():
        keep = keep_candidates(occs)
        if keep:
            for o in keep:
                o["quality"] = "high"
            index[text] = keep
        else:
            # Resurrect the best few so the n-gram stays usable, flagged low.
            low = sorted((o for o in occs if o["conf"] >= LOW_FLOOR),
                         key=lambda o: o["conf"], reverse=True)[:LOW_MAX]
            if low:
                for o in low:
                    o["quality"] = "low"
                index[text] = low
    return index, sorted(index.keys())


def ngram_quality(pool):
    """An n-gram is 'low' iff it has no high-tier clip (resurrected only)."""
    return "high" if any(o.get("quality") == "high" for o in pool) else "low"


def _print_stats(index):
    import statistics as st
    pools = [len(v) for v in index.values()]
    confs = [o["conf"] for v in index.values() for o in v]
    high = [t for t, v in index.items() if ngram_quality(v) == "high"]
    low = [t for t, v in index.items() if ngram_quality(v) == "low"]
    print(f"usable n-grams: {len(index)}  (high={len(high)}, "
          f"low/resurrected={len(low)})")
    print(f"total candidate clips: {sum(pools)}")
    print(f"pool size: mean={st.mean(pools):.1f} "
          f"median={st.median(pools):.0f} max={max(pools)}")
    print(f"kept conf: mean={st.mean(confs):.3f} median={st.median(confs):.3f}")

    print("\nper-n breakdown:")
    for n in range(1, 7):
        items = {t: v for t, v in index.items() if v[0]["n"] == n}
        if not items:
            continue
        c = [o["conf"] for v in items.values() for o in v]
        nlow = sum(1 for v in items.values() if ngram_quality(v) == "low")
        print(f"  n={n}: {len(items):6} texts  {len(c):7} clips  "
              f"conf mean={st.mean(c):.3f}  low={nlow}")

    print("\nexamples (n-gram: pool size, conf range):")
    for t in ["the", "what", "jerry", "i don't", "the night before",
              "do the opposite", "wait a second"]:
        v = index.get(t)
        if v:
            print(f"  {t:20} {len(v):3} clips  conf {v[-1]['conf']:.2f}-"
                  f"{v[0]['conf']:.2f}  ({ngram_quality(v)})")
        else:
            print(f"  {t:20} (not usable)")


if __name__ == "__main__":
    index, ngrams = build_ngram_candidate_index()
    _print_stats(index)
    if "--dump" in sys.argv:
        out = "ngramCandidates.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False)
        print(f"\nDumped index -> {out}")
