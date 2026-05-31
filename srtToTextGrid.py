"""
Build one Praat .TextGrid per episode from the matching .srt + .wav.

Each SRT subtitle becomes one interval on a single IntervalTier, padded on
each side so loose SRT timing comfortably brackets the real speech. The
resulting .wav + .TextGrid pair lets MFA align each subtitle line as its
own short utterance while keeping all output timestamps in the original
episode's time coordinate.

NOTE: Before running `mfa align`, remove or move the old per-episode .txt
transcripts in the same folder (e.g. `Remove-Item ./Seinfeld/Season1/*.txt`).
Otherwise MFA may treat them as the transcript instead of the TextGrid.
"""

import os

import pysrt
import soundfile
from praatio.data_classes.interval_tier import IntervalTier
from praatio.data_classes.textgrid import Textgrid
from praatio.utilities.constants import Interval

from sanatizeSRTsaveToTxt import clean_text, convert_numbers

SEASONS = range(1, 10)  # Season1 .. Season9
TIER_NAME = "speaker"
PAD_SEC = 0.5      # padding each side of every SRT interval
MIN_DUR = 0.10     # MFA silently skips intervals < 100 ms
TEST_MODE = False  # when True, build in-memory and print stats; don't write


def build_intervals(subs, duration):
    """Turn pysrt subtitles into a sorted, non-overlapping list of Intervals.

    Returns (intervals, stats) where stats has counts useful for TEST_MODE.
    """
    raw = []
    dropped_empty = 0
    for sub in subs:
        cleaned = clean_text(sub.text)
        if not cleaned:
            dropped_empty += 1
            continue
        text = convert_numbers(cleaned)
        start = max(0.0, sub.start.ordinal / 1000.0 - PAD_SEC)
        end = min(duration, sub.end.ordinal / 1000.0 + PAD_SEC)
        raw.append([start, end, text])

    raw.sort(key=lambda x: x[0])

    overlap_fixes = 0
    for i in range(len(raw) - 1):
        cur, nxt = raw[i], raw[i + 1]
        if cur[1] > nxt[0]:
            mid = (cur[1] + nxt[0]) / 2.0
            cur[1] = mid
            nxt[0] = mid
            overlap_fixes += 1

    intervals = []
    dropped_short = 0
    for start, end, text in raw:
        if end - start < MIN_DUR:
            dropped_short += 1
            continue
        intervals.append(Interval(start, end, text))

    stats = {
        "kept": len(intervals),
        "dropped_empty": dropped_empty,
        "dropped_short": dropped_short,
        "overlap_fixes": overlap_fixes,
    }
    return intervals, stats


def process_srts(folder_path, test_mode=False):
    if not os.path.isdir(folder_path):
        print(f"Error: '{folder_path}' is not a valid directory.")
        return

    srt_files = [f for f in os.listdir(folder_path) if f.lower().endswith(".srt")]
    if not srt_files:
        print("No .srt files found in the folder.")
        return

    mode_label = "TEST MODE (no files written)" if test_mode else "WRITE MODE"
    print(f"=== {mode_label} ===")
    print(f"Found {len(srt_files)} .srt file(s).\n")

    success, failed = 0, 0

    for filename in srt_files:
        srt_path = os.path.join(folder_path, filename)
        stem = os.path.splitext(filename)[0]
        wav_path = os.path.join(folder_path, stem + ".wav")
        out_path = os.path.join(folder_path, stem + ".TextGrid")

        if not os.path.exists(wav_path):
            print(f"[FAIL]    {filename} — matching .wav not found.")
            failed += 1
            continue

        try:
            duration = soundfile.info(wav_path).duration
            subs = pysrt.open(srt_path, error_handling=pysrt.ERROR_PASS)
            intervals, stats = build_intervals(subs, duration)

            if not intervals:
                print(f"[FAIL]    {filename} — no usable intervals after cleaning.")
                failed += 1
                continue

            tier = IntervalTier(TIER_NAME, intervals, minT=0.0, maxT=duration)
            tg = Textgrid(minTimestamp=0.0, maxTimestamp=duration)
            tg.addTier(tier)

            if test_mode:
                print(
                    f"[DRY]     {filename} → {stem}.TextGrid "
                    f"({stats['kept']} intervals, "
                    f"{stats['dropped_empty']} empty, "
                    f"{stats['dropped_short']} short, "
                    f"{stats['overlap_fixes']} overlap fixes)"
                )
            else:
                tg.save(
                    out_path,
                    format="long_textgrid",
                    includeBlankSpaces=True,
                )
                print(
                    f"[OK]      {filename} → {os.path.basename(out_path)} "
                    f"({stats['kept']} intervals)"
                )
            success += 1

        except Exception as e:
            print(f"[FAIL]    {filename} — error: {e}")
            failed += 1

    print("\n" + "=" * 50)
    if test_mode:
        print(f"Test complete. {success} file(s) scanned, {failed} failed.")
        print("No files were written. Set TEST_MODE = False to write .TextGrid files.")
    else:
        print(f"Done. ✓ {success} processed  ✗ {failed} failed.")


if __name__ == "__main__":
    for season in SEASONS:
        process_srts(f"./Seinfeld/Season{season}", test_mode=TEST_MODE)
