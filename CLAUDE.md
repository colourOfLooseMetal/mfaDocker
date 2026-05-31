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
6. `command.txt`              — Docker + `mfa align` invocations

`findNumbersInSubtitles.py` is a one-off helper to audit numeric lines.

## Files

```
CLAUDE.md                          — this file
command.txt                        — docker run + mfa align invocations
.gitignore                         — ignores *.mp4 *.mkv *.wav Seinfeld/

renameFiles.py                     — step 1: rename .mkv to sXXeYY
extractSubs.py                     — step 2: .mkv → .srt (ffmpeg, stream 0:s:0)
extractWavAudio.py                 — step 3: .mkv → 16 kHz mono .wav
sanatizeSRTsaveToTxt.py            — step 4: clean SRT, numerals → words → .txt
srtToTextGrid.py                   — step 5: .srt + .wav → Praat .TextGrid
                                     (imports clean_text + convert_numbers
                                     from sanatizeSRTsaveToTxt.py)
findNumbersInSubtitles.py          — ad-hoc: dump SRT lines containing digits
buildWordVideo.py                  — tkinter UI: pick words → ffmpeg clip
                                     mashup, reads wordTimings/ + Season1 .mkv

Seinfeld/SeasonN/                  — gitignored media + per-episode
                                     .srt / .wav / .txt / .TextGrid
output/sXXeYY.json                 — MFA alignment per episode
output/alignment_analysis.csv      — cross-episode summary
output/oov_counts_*.txt            — OOV word frequencies
output/oovs_found_*.txt            — OOV word list
output/utterance_oovs.txt          — OOVs grouped by utterance
wordTimings/sXXeYY.json            — post-processed per-episode word timings
wordVideos/                        — gitignored output mp4s from
                                     buildWordVideo.py
.idea/                             — JetBrains project config
```

## Runtime notes

- `srtToTextGrid.py` re-uses `clean_text` + `convert_numbers` from
  `sanatizeSRTsaveToTxt.py`; keep their signatures stable.
- MFA expects `.wav` + `.TextGrid` pairs in the same folder. The docstring in
  `srtToTextGrid.py` warns: delete stale `.txt` transcripts before
  `mfa align`, or MFA prefers them over the TextGrid.
- All step scripts have a top-of-file `FOLDER_PATH` constant (currently season-
  scoped). Edit per run.

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
