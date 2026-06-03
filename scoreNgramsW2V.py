"""
Per-n-gram acoustic confidence via wav2vec2 CTC.

The sibling script scoreWordsW2V.py scores single words; this scores every
contiguous n-gram (n = 1..6) *within a subtitle line*, so words and multi-word
phrases get a directly comparable confidence by the identical CTC code path. In
the eventual sentence-builder a 1-gram and a 6-gram are the same kind of puzzle
piece, so they must be measured the same way.

n-grams never cross line boundaries: a line of W words yields, per n, W-n+1
contiguous windows (n=1 is the single-word case). Line -> words grouping comes
from dialogue.json (the combined MFA index). The audio of each window
(first-word start .. last-word end, padded) is asked, via a wav2vec2 CTC model,
how confidently it "spells" the joined words; the score is the length-normalized
CTC probability of the target character sequence (words joined by the model's
word-delimiter token). Greedy transcription + match flag are kept for QA.

Output mirrors dialogue.json's shape (per episode -> lines -> ngrams) so it
joins straight back to the per-word data by (stem, line index, i, n).

Streams one episode at a time and batches clips to the GPU. Resumable:
episodes whose output JSON already exists are skipped.

Requires (Blackwell / RTX 50-series needs the cu128 build):
    pip install torch --index-url https://download.pytorch.org/whl/cu128
    pip install transformers
"""

import json
import math
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
import torch.nn.functional as F
from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor

from scoreWordsW2V import greedy_decode

DIALOGUE_JSON = r"./dialogue.json"     # line -> words source of truth
WAV_DIR = r"./Seinfeld/allWavs"        # 16 kHz mono wavs (input)
OUT_DIR = r"./ngramScores"             # per-episode JSON output
MODEL_ID = "facebook/wav2vec2-large-robust-ft-libri-960h"
NGRAM_MIN, NGRAM_MAX = 1, 6            # 1-grams (words) scored like n-grams
PAD_SEC = 0.05                         # 50 ms each side, same as word scorer
BATCH_SIZE = 64                        # clips per GPU batch
SR = 16000

# Test-subset knob (set to None for the full 171-episode run):
MAX_EPISODES = None


def word_ids(tokenizer, word):
    """Map one word's uppercase characters to vocab ids (skip unknowns).

    Same logic as scoreWordsW2V.target_ids; the per-word building block that
    keeps n=1 byte-identical to the word scorer.
    """
    ids = []
    for ch in word.upper():
        tid = tokenizer.convert_tokens_to_ids(ch)
        if tid is None or tid == tokenizer.unk_token_id:
            continue
        ids.append(tid)
    return ids


def ngram_target_ids(tokenizer, words, delim_id):
    """CTC target for a window: each word's char ids joined by the word
    delimiter. For a single word this is exactly word_ids (no delimiter)."""
    ids = []
    for k, w in enumerate(words):
        if k:
            ids.append(delim_id)
        ids.extend(word_ids(tokenizer, w))
    return ids


def enumerate_ngrams(lines):
    """Flatten dialogue.json lines into n-gram tasks.

    Returns a list of dicts, one per window, each with line_idx, n, i (start
    word index in the line), text, start, end.
    """
    tasks = []
    for line_idx, line in enumerate(lines):
        words = line["words"]
        for n in range(NGRAM_MIN, NGRAM_MAX + 1):
            for i in range(len(words) - n + 1):
                window = words[i:i + n]
                tasks.append({
                    "line_idx": line_idx,
                    "n": n,
                    "i": i,
                    "text": " ".join(w["word"] for w in window),
                    "words": [w["word"] for w in window],
                    "start": float(window[0]["start"]),
                    "end": float(window[-1]["end"]),
                })
    return tasks


def score_episode(lines, audio, dur, model, processor, device, blank_id,
                  delim_id, frame_len_fn):
    """Score every n-gram in one episode; return per-task records aligned to
    enumerate_ngrams(lines)."""
    tasks = enumerate_ngrams(lines)

    # Pre-slice clips (first-word start .. last-word end, padded), same scheme
    # as the word scorer.
    clips = []
    for t in tasks:
        s = max(0.0, t["start"] - PAD_SEC)
        e = min(dur, t["end"] + PAD_SEC)
        clip = audio[int(s * SR):int(e * SR)]
        if clip.size == 0:
            clip = np.zeros(1, dtype="float32")
        clips.append(clip)

    records = [None] * len(tasks)
    for b in range(0, len(tasks), BATCH_SIZE):
        batch_idx = range(b, min(b + BATCH_SIZE, len(tasks)))
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
            t = tasks[i]
            n_frames = int(frame_len_fn(valid_lens[j]))
            n_frames = max(1, min(n_frames, logp.shape[1]))
            clip_logp = logp[j, :n_frames]  # (n, V)

            tids = ngram_target_ids(processor.tokenizer, t["words"], delim_id)
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
                "n": t["n"],
                "i": t["i"],
                "text": t["text"],
                "start": round(t["start"], 3),
                "end": round(t["end"], 3),
                "conf": None if conf is None else round(conf, 4),
                "logprob": None if avg_lp is None else round(avg_lp, 4),
                "greedy": greedy,
                "match": (greedy == t["text"].upper()),
                "note": note,
            }

    # Scatter flat records back under their lines, mirroring dialogue.json.
    out_lines = []
    for line_idx, line in enumerate(lines):
        grams = [r for r, t in zip(records, tasks)
                 if t["line_idx"] == line_idx]
        if not grams:
            continue
        out_lines.append({
            "line_idx": line_idx,
            "xmin": line["xmin"],
            "xmax": line["xmax"],
            "text": line["text"],
            "ngrams": grams,
        })
    return out_lines, records


def main():
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Loading {MODEL_ID} on {device} ...")
    processor = Wav2Vec2Processor.from_pretrained(MODEL_ID)
    model = Wav2Vec2ForCTC.from_pretrained(MODEL_ID).to(device).eval()
    if device == "cuda":
        model = model.half()
    blank_id = model.config.pad_token_id
    delim_id = processor.tokenizer.convert_tokens_to_ids(
        processor.tokenizer.word_delimiter_token)
    frame_len_fn = model._get_feat_extract_output_lengths

    out_root = Path(OUT_DIR)
    out_root.mkdir(parents=True, exist_ok=True)

    with open(DIALOGUE_JSON, encoding="utf-8") as f:
        index = json.load(f)
    stems = sorted(index)
    if MAX_EPISODES is not None:
        stems = stems[:MAX_EPISODES]

    grand_ngrams = grand_match = 0
    grand_conf_sum = 0.0
    for n, stem in enumerate(stems, 1):
        out_path = out_root / f"{stem}.json"
        if out_path.exists():
            print(f"[SKIP] {stem} (exists)")
            continue

        wav_path = Path(WAV_DIR) / f"{stem}.wav"
        audio, sr = sf.read(wav_path, dtype="float32")
        if sr != SR:
            raise ValueError(f"{stem}: expected {SR} Hz, got {sr}")
        if audio.ndim > 1:
            audio = audio[:, 0]
        dur = len(audio) / SR

        t0 = time.time()
        out_lines, records = score_episode(
            index[stem]["lines"], audio, dur, model, processor, device,
            blank_id, delim_id, frame_len_fn)
        dt = time.time() - t0

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({
                "episode": stem,
                "model": MODEL_ID,
                "pad_sec": PAD_SEC,
                "ngram_range": [NGRAM_MIN, NGRAM_MAX],
                "lines": out_lines,
            }, f, ensure_ascii=False)

        scored = [r for r in records if r["conf"] is not None]
        mean_conf = (sum(r["conf"] for r in scored) / len(scored)
                     if scored else 0.0)
        match_rate = (sum(1 for r in records if r["match"]) / len(records)
                      if records else 0.0)
        nps = len(records) / dt if dt else 0.0
        grand_ngrams += len(records)
        grand_match += sum(1 for r in records if r["match"])
        grand_conf_sum += sum(r["conf"] for r in scored)
        remaining = len(stems) - n
        eta = remaining * dt
        print(f"[OK]   {stem}: {len(records):5} ngrams  "
              f"mean_conf={mean_conf:.3f}  match={match_rate:.0%}  "
              f"{nps:.0f} clips/s  ({dt:.1f}s, ~{eta/60:.1f} min left)")

    if grand_ngrams:
        print(f"\nTotal: {grand_ngrams} ngrams, "
              f"global mean_conf={grand_conf_sum / grand_ngrams:.3f}, "
              f"match={grand_match / grand_ngrams:.0%}")


if __name__ == "__main__":
    main()
