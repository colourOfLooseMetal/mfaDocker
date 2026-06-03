# mfaDocker

Pipeline for running **Montreal Forced Aligner** in Docker to time-align Seinfeld
episode subtitles against episode audio. SRT + WAV → Praat TextGrid → MFA → JSON
word timings.

## Pipeline order

1. `renameFiles.py`           — normalize `.mkv` names to `sXXeYY.mkv`
2. `extractSubs.py`           — `.mkv` → `.srt` (ffmpeg, stream 0:s:0)
3. `extractWavAudio.py`       — `.mkv` → 16 kHz mono `.wav` (ffmpeg)
4. `sanatizeSRTsaveToTxt.py`  — clean SRT, expand numerals to words → `.txt`
5. `srtToTextGrid.py`         — `.srt` + `.wav` → padded `.TextGrid` for MFA
6. `command.txt`              — Docker + `mfa align` invocations. ran by user not claude agent
7. `buildDialogueIndex.py`    — join MFA output (`output_final/`) + line bounds
                                (`Seinfeld/textgrids/`) → combined `dialogue.json`

`findNumbersInSubtitles.py` is a one-off helper to audit numeric lines.

## Files

```
CLAUDE.md                          — this file
command.txt                        — docker run + mfa align invocations ran only by user not claude
.gitignore                         — ignores *.mp4 *.mkv *.wav Seinfeld/
                                     wordVideos/ wordClips/ lineClips/
                                     web/ffmpeg/*.wasm

renameFiles.py                     — step 1: rename .mkv to sXXeYY
extractSubs.py                     — step 2: .mkv → .srt (ffmpeg, stream 0:s:0)
extractWavAudio.py                 — step 3: .mkv → 16 kHz mono .wav
sanatizeSRTsaveToTxt.py            — step 4: clean SRT, numerals → words → .txt
srtToTextGrid.py                   — step 5: .srt + .wav → Praat .TextGrid
                                     (imports clean_text + convert_numbers
                                     from sanatizeSRTsaveToTxt.py)
findNumbersInSubtitles.py          — ad-hoc: dump SRT lines containing digits
buildNgramVideo.py                 — tkinter UI: pick n-grams → ffmpeg clip
                                     mashup. Prefers lineClips/ (all n-grams),
                                     then wordClips/, then live extraction
                                     from allEpisodes/
scoreWordsW2V.py                    — wav2vec2 CTC per-word acoustic confidence
                                     (slices allWavs/ at MFA bounds, GPU-batched)
                                     → wordScores/sXXeYY.json
scoreNgramsW2V.py                   — same CTC scoring extended to every
                                     contiguous n-gram (n=1–6, words and phrases
                                     scored identically) within each
                                     dialogue.json line (no cross-line windows)
                                     → ngramScores/sXXeYY.json
sampleConfidenceBands.py           — render subtitled word clips grouped by
                                     confidence (relative deciles / absolute
                                     bands) for manual QA; reuses
                                     extract_segment from buildWordVideo.py
              
confHistogram.py                   — conf distribution chart + CSV (matplotlib)
wordCandidates.py                  — confidence cutoff (quality-first per-word
                                     pool, low-quality resurrection) +
                                     weighted_pick; feeds buildWordVideo.py
cutWordClips.py                    — pre-cut accepted clips (subtitle burned,
                                     720x540 normalized) → wordClips/<word>/ +
                                     index.json; builder needs no source .mkv
ngramCandidates.py                 — n-gram analogue of wordCandidates.py:
                                     per-n quality-first cutoff over
                                     ngramScores/ (rare-text resurrection) →
                                     candidate pool per n-gram text; importable
                                     by cutLineClips.py, --dump writes
                                     ngramCandidates.json
cutLineClips.py                    — cut each line holding a candidate n-gram
                                     → lineClips/<stem>/L<idx>.mp4 (720x540,
                                     word-level karaoke captions) + inverted
                                     index.json (n-gram → occurrences with clip
                                     file + in-clip start/end). Lines with no
                                     candidate aren't cut
buildDialogueIndex.py              — step 7: join output_final/ word timings +
                                     textgrid line bounds → dialogue.json (words
                                     grouped into subtitle lines, by midpoint) +
                                     dialogue.sample.json
web/                               — browser frontend: retro Seinfeld page +
                                     client-side ffmpeg.wasm n-gram mashup.
                                     Loads lineClips/index.json; trims each
                                     picked n-gram out of its sfvL_*.mp4 line
                                     clip (joinSegments in js/ffmpeg.js).
                                     CLIPS_BASE in js/clipIndex.js defaults to
                                     /lineClips/ for dev; swap for DO Spaces CDN
                                     (colm-extra-storage.nyc3.cdn.digitaloceanspaces.com/sfLines/)
                                     in production. (index.html, themes/, js/,
                                     ffmpeg/, serve.py)
web/README.md                      — setup, ffmpeg vendor re-fetch commands,
                                     CLIPS_BASE / CDN swap instructions

Seinfeld/allEpisodes/sXXeYY.mkv    — gitignored flat: every episode .mkv
Seinfeld/allWavs/sXXeYY.wav        — gitignored flat: 16 kHz mono .wav
Seinfeld/srts/sXXeYY.srt           — gitignored flat: extracted subtitles
Seinfeld/textgrids/sXXeYY.TextGrid — gitignored flat: MFA input TextGrids
output_final/sXXeYY.json           — final MFA word/phone timings (all seasons)
dialogue.json                      — single combined index: MFA word timings
                                     grouped into aligned subtitle lines, keyed
                                     by episode. One source of truth replacing
                                     per-op joins of output_final/ + textgrids/
dialogue.sample.json               — tiny excerpt (eps 1–5, first 3 lines each)
                                     showing dialogue.json's structure; inspect
                                     this instead of opening the large full file

wordTimings/sXXeYY.json            — post-processed per-episode word timings
wordVideos/                        — gitignored output mp4s from
                                     buildWordVideo.py
wordVideos/quality_samples/        — per-category sample clips + manifest.csv
                                     from sampleAlignmentQuality.py
wordScores/sXXeYY.json             — per-word wav2vec2 confidence + greedy
                                     decode + prev/next, from scoreWordsW2V.py
ngramScores/sXXeYY.json            — per-line nested n-gram span scores (conf +
                                     greedy + match, n=1–6), from
                                     scoreNgramsW2V.py
ngramCandidates.json               — --dump of ngramCandidates.py: post-cutoff
                                     candidate pool per n-gram text
lineClips/sfvL_<N>.mp4             — gitignored per-line karaoke-captioned
                                     clips (flat, show-wide sequential IDs) +
                                     index.json inverted manifest, from
                                     cutLineClips.py
wordClips/<word>/*.mp4             — gitignored pre-cut subtitled word clips +
                                     index.json manifest, from cutWordClips.py
wordVideos/confSamples/            — conf-banded QA clips + manifest.csv +
                                     conf_distribution.png/.csv
.idea/                             — JetBrains project config
```

## Runtime notes

- `srtToTextGrid.py` re-uses `clean_text` + `convert_numbers` from
  `sanatizeSRTsaveToTxt.py`; keep their signatures stable.
- MFA expects `.wav` + `.TextGrid` pairs in the same folder. The docstring in
  `srtToTextGrid.py` warns: delete stale `.txt` transcripts before
  `mfa align`, or MFA prefers them over the TextGrid.
- The per-folder step scripts loop all seasons via a top-of-file `SEASONS`
  range (`range(1, 10)`; `renameFiles.py` uses `range(2, 10)` to skip the
  already-renamed Season 1) and build `./Seinfeld/Season{n}` paths.
  `renameFiles.py` derives `sNNeMM` names by parsing `SxxEyy` from each
  source filename, so it is idempotent and safe to re-run.
- Episodes mix 4:3 and 16:9 source resolutions, so `extract_segment` takes
  `scale_to=(CLIP_W, CLIP_H)` (720x540) to normalize every clip — required for
  `-c copy` concat (desktop and web). `cutWordClips.py` and the mkv-fallback
  path pass it; the caption sidecar is written to a temp file so apostrophe
  words ("don't", "it's") don't break drawtext's textfile path.

## Maintaining this file

When work in this repo adds or removes a top-level file or folder, update the
**Files** section above. Keep entries in the existing style:

- One line per path.
- Path on the left, ` — ` separator, brief purpose on the right.
- Aim for ≤ 80 chars per line; wrap continuation lines indented under the
  purpose column (see `srtToTextGrid.py` for an example).
- Group related outputs with a glob (`output/oov_counts_*.txt`) rather than
  listing each variant.
- If a new pipeline step is added, give it a `step N:` prefix and renumber if
  inserted mid-pipeline; also update the **Pipeline order** section at the top.
- Do **not** expand into a full tree, add per-file docstrings here, or document
  internal functions — that belongs in the source file itself.
- Remove entries when their file is deleted; don't leave tombstones.
