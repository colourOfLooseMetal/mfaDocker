"""
Per-word acoustic confidence via wav2vec2 CTC.

For each MFA-aligned word, slice its audio (with a little padding) from the
16 kHz episode wav and ask a wav2vec2 CTC model how confidently the clip
"spells" that word. The score is the length-normalized CTC probability of the
target character sequence; we also store the model's greedy transcription and
a match flag for inspection. Results land in per-episode JSON under OUT_DIR.

Streams one episode at a time and batches clips to the GPU, so RAM/VRAM stay
bounded. Resumable: episodes whose output JSON already exists are skipped.

Requires (Blackwell / RTX 50-series needs the cu128 build):
    pip install torch --index-url https://download.pytorch.org/whl/cu128
    pip install transformers
"""

import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
import torch.nn.functional as F
from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor

JSON_DIR = r"./output_final"          # MFA word timings (input)
WAV_DIR = r"./Seinfeld/allWavs"       # 16 kHz mono wavs (input)
OUT_DIR = r"./wordScores"             # per-episode JSON output
MODEL_ID = "facebook/wav2vec2-large-robust-ft-libri-960h"
PAD_SEC = 0.05                         # 50 ms each side
BATCH_SIZE = 64                        # clips per GPU batch
SR = 16000

# Test-subset knobs (set both to None for the full run):
MAX_EPISODES = None   # process at most this many episode JSONs
MAX_WORDS = None      # cap words scored per episode (None = all)


def load_words(json_path):
    """Return ordered list of (start, end, word) for real words only."""
    with open(json_path, encoding="utf-8") as f:
        entries = json.load(f)["tiers"]["words"]["entries"]
    out = []
    for start, end, word in entries:
        w = (word or "").strip()
        if not w or (w.startswith("<") and w.endswith(">")):
            continue
        out.append((float(start), float(end), w))
    return out


def target_ids(tokenizer, word):
    """Map a word's uppercase characters to vocab ids (skip unknowns)."""
    ids = []
    for ch in word.upper():
        tid = tokenizer.convert_tokens_to_ids(ch)
        if tid is None or tid == tokenizer.unk_token_id:
            continue
        ids.append(tid)
    return ids


def greedy_decode(processor, logp_clip):
    """Argmax → collapse repeats → drop blanks → string."""
    ids = logp_clip.argmax(dim=-1).tolist()
    return processor.decode(ids).strip().upper()


def score_episode(stem, model, processor, device, blank_id, frame_len_fn):
    """Score every (capped) word in one episode; return list of records."""
    wav_path = Path(WAV_DIR) / f"{stem}.wav"
    audio, sr = sf.read(wav_path, dtype="float32")
    if sr != SR:
        raise ValueError(f"{stem}: expected {SR} Hz, got {sr}")
    if audio.ndim > 1:
        audio = audio[:, 0]
    dur = len(audio) / SR

    words = load_words(Path(JSON_DIR) / f"{stem}.json")
    if MAX_WORDS is not None:
        words = words[:MAX_WORDS]

    records = [None] * len(words)
    # Pre-slice clips with neighbour context for prev/next.
    clips = []
    for i, (start, end, word) in enumerate(words):
        s = max(0.0, start - PAD_SEC)
        e = min(dur, end + PAD_SEC)
        clip = audio[int(s * SR):int(e * SR)]
        if clip.size == 0:
            clip = np.zeros(1, dtype="float32")
        clips.append(clip)

    for b in range(0, len(words), BATCH_SIZE):
        batch_idx = range(b, min(b + BATCH_SIZE, len(words)))
        batch_clips = [clips[i] for i in batch_idx]
        valid_lens = [len(c) for c in batch_clips]

        proc = processor(batch_clips, sampling_rate=SR,
                         return_tensors="pt", padding=True,
                         return_attention_mask=True)
        input_values = proc.input_values.to(device)
        attention_mask = proc.attention_mask.to(device)
        if device == "cuda":
            input_values = input_values.half()

        with torch.inference_mode():
            # attention_mask is REQUIRED for layer-norm models (large-robust):
            # without it, zero-padding corrupts the shorter clips in a batch.
            logits = model(input_values, attention_mask=attention_mask).logits
            logp = F.log_softmax(logits.float(), dim=-1)

        for j, i in enumerate(batch_idx):
            start, end, word = words[i]
            n_frames = int(frame_len_fn(valid_lens[j]))
            n_frames = max(1, min(n_frames, logp.shape[1]))
            clip_logp = logp[j, :n_frames]  # (n, V)

            tids = target_ids(processor.tokenizer, word)
            greedy = greedy_decode(processor, clip_logp)
            note = None
            conf = None
            avg_lp = None

            if not tids:
                note = "no_target_tokens"
            elif n_frames < len(tids):
                note = "too_short"
            else:
                target = torch.tensor(tids, dtype=torch.long)
                nll = F.ctc_loss(
                    clip_logp.unsqueeze(1),                 # (T, 1, V)
                    target,                                 # (sum target_len)
                    torch.tensor([n_frames], dtype=torch.long),
                    torch.tensor([len(tids)], dtype=torch.long),
                    blank=blank_id, reduction="sum", zero_infinity=True,
                )
                avg_lp = float(-nll.item() / len(tids))
                conf = math.exp(avg_lp) if avg_lp < 0 else 1.0

            records[i] = {
                "word": word,
                "start": round(start, 3),
                "end": round(end, 3),
                "conf": None if conf is None else round(conf, 4),
                "logprob": None if avg_lp is None else round(avg_lp, 4),
                "greedy": greedy,
                "match": (greedy == word.upper()),
                "prev": words[i - 1][2] if i > 0 else None,
                "next": words[i + 1][2] if i + 1 < len(words) else None,
                "note": note,
            }

    return records


def main():
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Loading {MODEL_ID} on {device} …")
    processor = Wav2Vec2Processor.from_pretrained(MODEL_ID)
    model = Wav2Vec2ForCTC.from_pretrained(MODEL_ID).to(device).eval()
    if device == "cuda":
        model = model.half()
    blank_id = model.config.pad_token_id
    frame_len_fn = model._get_feat_extract_output_lengths

    out_root = Path(OUT_DIR)
    out_root.mkdir(parents=True, exist_ok=True)

    stems = sorted(p.stem for p in Path(JSON_DIR).glob("*.json"))
    if MAX_EPISODES is not None:
        stems = stems[:MAX_EPISODES]

    grand_words = grand_match = 0
    grand_conf_sum = 0.0
    for n, stem in enumerate(stems, 1):
        out_path = out_root / f"{stem}.json"
        if out_path.exists():
            print(f"[SKIP] {stem} (exists)")
            continue
        t0 = time.time()
        records = score_episode(stem, model, processor, device,
                                blank_id, frame_len_fn)
        dt = time.time() - t0

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({
                "episode": stem,
                "model": MODEL_ID,
                "pad_sec": PAD_SEC,
                "words": records,
            }, f, ensure_ascii=False)

        scored = [r for r in records if r["conf"] is not None]
        mean_conf = (sum(r["conf"] for r in scored) / len(scored)
                     if scored else 0.0)
        match_rate = (sum(1 for r in records if r["match"]) / len(records)
                      if records else 0.0)
        cps = len(records) / dt if dt else 0.0
        grand_words += len(records)
        grand_match += sum(1 for r in records if r["match"])
        grand_conf_sum += sum(r["conf"] for r in scored)
        remaining = len(stems) - n
        eta = remaining * dt
        print(f"[OK]   {stem}: {len(records):4} words  "
              f"mean_conf={mean_conf:.3f}  match={match_rate:.0%}  "
              f"{cps:.0f} clips/s  ({dt:.1f}s, ~{eta/60:.1f} min left)")

    if grand_words:
        print(f"\nTotal: {grand_words} words, "
              f"global mean_conf={grand_conf_sum / grand_words:.3f}, "
              f"match={grand_match / grand_words:.0%}")


if __name__ == "__main__":
    main()
