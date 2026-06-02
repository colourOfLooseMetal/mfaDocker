"""
Confidence-aware word-candidate index.

Reads per-word wav2vec2 confidences from wordScores/*.json and applies a
quality-first cutoff to produce, per word, a small pool of the best clips to
use when building sentences. Importable by buildWordVideo.py (and samplers);
run directly to print cutoff stats or dump the index.

Cutoff per word:
  floor = RARE_FLOOR if total occurrences <= RARE_OCC_MAX else ABS_FLOOR
  survivors = occurrences with conf >= floor, sorted by conf desc
  target = min(MAX_PER_WORD, max(MIN_KEEP, ceil(TARGET_FRAC * n_survivors)))
  walk survivors high->low, keeping until `target`, but stop early once a clip
  is below GOOD and we already hold MIN_KEEP (don't pad the pool with weak
  clips when enough good ones exist).

Selection: confidence-weighted random, weight = conf ** WEIGHT_POWER.
"""

import glob
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path

WORDSCORES_DIR = r"./wordScores"

ABS_FLOOR = 0.12       # floor for normal words
RARE_FLOOR = 0.05      # floor for rare words (<= RARE_OCC_MAX total occ)
RARE_OCC_MAX = 3
GOOD = 0.20            # preferred-quality threshold
MIN_KEEP = 3           # keep at least this many when available, even if < GOOD
MAX_PER_WORD = 50      # hard cap on pool size
TARGET_FRAC = 0.50     # ~half retention target (capped by MAX_PER_WORD)
WEIGHT_POWER = 2       # selection weight = conf ** WEIGHT_POWER

# Low-quality resurrection: ONLY for words with no high-tier clip, so words
# don't vanish from the vocabulary. These are flagged quality="low".
LOW_FLOOR = 0.02       # drop truly-broken clips (~0 conf misalignments)
LOW_MAX = 5            # best N clips to resurrect for an otherwise-unavailable word


def keep_candidates(occs):
    """Apply the quality-first cutoff to one word's occurrences.

    `occs`: list of dicts with at least "conf". Returns the kept sublist
    (highest-conf first). Empty if nothing clears the floor.
    """
    floor = RARE_FLOOR if len(occs) <= RARE_OCC_MAX else ABS_FLOOR
    survivors = sorted((o for o in occs if o["conf"] >= floor),
                       key=lambda o: o["conf"], reverse=True)
    n = len(survivors)
    if n == 0:
        return []
    target = min(MAX_PER_WORD, max(MIN_KEEP, math.ceil(TARGET_FRAC * n)))
    keep = []
    for o in survivors:
        if len(keep) >= target:
            break
        if o["conf"] < GOOD and len(keep) >= MIN_KEEP:
            break
        keep.append(o)
    return keep


def build_candidate_index(wordscores_dir=WORDSCORES_DIR):
    """Return (index, words).

    index = {word: [{"stem","start","end","conf"}, ...]}  (post-cutoff pool)
    words = sorted list of usable words.
    """
    by_word = defaultdict(list)
    for p in glob.glob(f"{wordscores_dir}/*.json"):
        stem = Path(p).stem
        for r in json.load(open(p, encoding="utf-8"))["words"]:
            if r["conf"] is None:
                continue
            w = r["word"]
            # Drop empties, MFA <tokens>, and non-lexical fragments that don't
            # start with a letter ("'ve", "'s", "-p", ...).
            if not w or not w[0].isalpha():
                continue
            by_word[w].append({
                "stem": stem, "start": r["start"], "end": r["end"],
                "conf": r["conf"],
            })

    index = {}
    for w, occs in by_word.items():
        keep = keep_candidates(occs)
        if keep:
            for o in keep:
                o["quality"] = "high"
            index[w] = keep
        else:
            # Resurrect the best few clips so the word stays usable, flagged low.
            low = sorted((o for o in occs if o["conf"] >= LOW_FLOOR),
                         key=lambda o: o["conf"], reverse=True)[:LOW_MAX]
            if low:
                for o in low:
                    o["quality"] = "low"
                index[w] = low
    return index, sorted(index.keys())


def word_quality(pool):
    """A word is 'low' iff it has no high-tier clip (resurrected only)."""
    return "high" if any(o.get("quality") == "high" for o in pool) else "low"


def weighted_pick(keep, rng):
    """Confidence-weighted random choice from a kept pool."""
    weights = [o["conf"] ** WEIGHT_POWER for o in keep]
    return rng.choices(keep, weights=weights, k=1)[0]


def _print_stats(index):
    import statistics as st
    pools = [len(v) for v in index.values()]
    confs = [o["conf"] for v in index.values() for o in v]
    high_words = [w for w, v in index.items() if word_quality(v) == "high"]
    low_words = [w for w, v in index.items() if word_quality(v) == "low"]
    print(f"usable words: {len(index)}  (high={len(high_words)}, "
          f"low/resurrected={len(low_words)})")
    print(f"total candidate clips: {sum(pools)}")
    print(f"pool size: mean={st.mean(pools):.1f} "
          f"median={st.median(pools):.0f} max={max(pools)}")
    print(f"kept conf: mean={st.mean(confs):.3f} median={st.median(confs):.3f}")
    print(f"kept clips below GOOD ({GOOD}): "
          f"{100 * sum(1 for c in confs if c < GOOD) / len(confs):.1f}%")
    print("\nexamples (word: pool size, conf range):")
    for w in ["the", "what", "jerry", "to", "a", "no", "yeah", "hello"]:
        v = index.get(w)
        if v:
            print(f"  {w:8} {len(v):3} clips  conf {v[-1]['conf']:.2f}-"
                  f"{v[0]['conf']:.2f}")
        else:
            print(f"  {w:8} (not usable)")


if __name__ == "__main__":
    index, words = build_candidate_index()
    _print_stats(index)
    if "--dump" in sys.argv:
        out = "wordCandidates.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False)
        print(f"\nDumped index -> {out}")
