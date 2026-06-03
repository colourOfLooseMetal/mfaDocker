"""
Pick n-grams → ffmpeg clip mashup.

Offers an autocomplete UI to choose a sequence of n-grams (words or phrases),
picks a clip per entry from lineClips/index.json, and stitches them into a
single .mp4 in OUTPUT_DIR. Falls back to pre-cut wordClips/ or live .mkv
extraction if lineClips/ is not available.
"""

import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from wordCandidates import build_candidate_index, weighted_pick, word_quality

JSON_DIR = r"./output_final"
WORDSCORES_DIR = r"./wordScores"
MKV_DIR = r"./Seinfeld/allEpisodes"
LINE_CLIPS_DIR = r"./lineClips"   # per-line karaoke clips + inverted index
CLIPS_DIR = r"./wordClips"
OUTPUT_DIR = r"./wordVideos"

PAD_SEC = 0.02
TARGET_FPS = 30
TARGET_AR = 48000
TARGET_CRF = 22
MAX_HITS = 8

# Normalized clip canvas (4:3). Mixed-resolution source episodes are scaled to
# fill + center-cropped to this exact size so all clips concat-copy safely.
CLIP_W, CLIP_H = 640, 360

# Burned-in captions
BURN_SUBS_DEFAULT = True
CAPTION_FONT = r"C:/Windows/Fonts/arial.ttf"
CAPTION_FONTSIZE = 96
CAPTION_BORDER = 4
CAPTION_BOTTOM_PAD = 60  # pixels from bottom edge

# Held-frame + silent-audio gap inserted after every non-last word.
# Controlled live from the UI; these are the slider's default and upper bound.
GAP_DEFAULT_SEC = 0.10     # default silence inserted between word clips
GAP_MAX_SEC = 0.50         # slider upper bound


# --- Word index ---------------------------------------------------------------

def build_index(json_dir, mkv_dir):
    """Return {word: [(stem, start, end), ...]} and the sorted word list.

    Drops occurrences whose source .mkv is missing, with a [WARN] log.
    """
    json_dir = Path(json_dir)
    mkv_dir = Path(mkv_dir)

    if not json_dir.is_dir():
        print(f"[ERROR] JSON_DIR not found: {json_dir}", file=sys.stderr)
        return {}, []

    index = {}
    episodes_seen = set()
    for jpath in sorted(json_dir.glob("*.json")):
        stem = jpath.stem
        episodes_seen.add(stem)
        try:
            with open(jpath, encoding="utf-8") as f:
                data = json.load(f)
            entries = data["tiers"]["words"]["entries"]
        except (OSError, KeyError, json.JSONDecodeError) as e:
            print(f"[WARN] could not parse {jpath.name}: {e}", file=sys.stderr)
            continue
        for start, end, word in entries:
            if not word or not word.strip():
                continue
            w = word.lower().strip()
            if w.startswith("<") and w.endswith(">"):
                continue  # MFA special tokens like <unk>
            index.setdefault(w, []).append((stem, float(start), float(end)))

    # Drop occurrences whose source .mkv is missing.
    missing = {s for s in episodes_seen if not (mkv_dir / f"{s}.mkv").exists()}
    if missing:
        for word, occs in list(index.items()):
            kept = [o for o in occs if o[0] not in missing]
            dropped = len(occs) - len(kept)
            if not kept:
                del index[word]
            elif dropped:
                index[word] = kept
        for stem in sorted(missing):
            print(f"[WARN] no MKV for {stem}, dropped its occurrences",
                  file=sys.stderr)

    words = sorted(index.keys())
    print(f"indexed {len(words)} unique words across "
          f"{len(episodes_seen - missing)} episode(s)")
    return index, words


# --- Build pipeline -----------------------------------------------------------

def sanitize_filename(words):
    name = "_".join(words).lower()
    name = re.sub(r"[^a-z0-9_]", "", name)
    return name[:100] or "untitled"


def unique_output_path(out_dir, base, ext=".mp4"):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    candidate = out_dir / f"{base}{ext}"
    n = 2
    while candidate.exists():
        candidate = out_dir / f"{base}_{n}{ext}"
        n += 1
    return candidate


def _ffmpeg_path_escape(p):
    """Escape a path for use as an ffmpeg filter value.

    Even inside single quotes, ffmpeg's filter-arg parser still treats ':' as
    a k=v separator on Windows paths like 'C:/foo'. Escape ':' (and '\\', '\\'')
    explicitly. Forward-slash the path so we don't have to escape backslashes
    in the file separators themselves.
    """
    p = str(p).replace("\\", "/")
    return (
        p.replace("\\", "\\\\")
         .replace("'", r"\'")
         .replace(":", r"\:")
    )


def extract_segment(mkv_path, start, end, out_path, caption=None, pad_after=0.0,
                    scale_to=None):
    """Fast-seek extract one clip, re-encode to locked params.

    If `caption` is provided, write it to a sidecar .txt next to out_path and
    burn it into the bottom of the frame with drawtext.

    If `pad_after` > 0, freeze the last frame and append true silence for
    that many seconds. The pad path uses an `anullsrc` virtual input and
    `concat` filter for audio so no source audio bleeds past the word's MFA
    end boundary.

    If `scale_to=(W, H)`, scale-to-fill + center-crop every clip to that exact
    canvas so a mixed-resolution corpus (4:3 + 16:9 episodes) yields uniform
    clips that concat-copy safely. Applied AFTER the caption so the burned text
    scales proportionally with the frame.
    """
    duration = max(end - start, 0.01)
    pad = max(0.0, float(pad_after))

    scale_filters = []
    if scale_to:
        sw, sh = scale_to
        scale_filters = [
            f"scale={sw}:{sh}:force_original_aspect_ratio=increase",
            f"crop={sw}:{sh}", "setsar=1",
        ]

    # Build the drawtext filter once if a caption was requested. The caption
    # text goes in a temp file (NOT next to out_path) so an apostrophe in the
    # output folder — e.g. words like "don't"/"it's" — can't break drawtext's
    # textfile path parsing.
    drawtext_filter = None
    caption_path = None
    if caption:
        fd, cap = tempfile.mkstemp(prefix="cap_", suffix=".txt")
        os.close(fd)
        caption_path = Path(cap)
        caption_path.write_text(caption, encoding="utf-8")
        textfile_arg = _ffmpeg_path_escape(caption_path)
        fontfile_arg = _ffmpeg_path_escape(CAPTION_FONT)
        drawtext_filter = (
            f"drawtext=textfile='{textfile_arg}'"
            f":fontfile='{fontfile_arg}'"
            f":fontsize={CAPTION_FONTSIZE}"
            f":fontcolor=white"
            f":borderw={CAPTION_BORDER}"
            f":bordercolor=black"
            f":x=(w-text_w)/2"
            f":y=h-text_h-{CAPTION_BOTTOM_PAD}"
        )

    if pad <= 0:
        # No pad: original known-good single-input path with output-side -t.
        vf_parts = [f"fps={TARGET_FPS}", "setsar=1", "format=yuv420p"]
        if drawtext_filter:
            vf_parts.append(drawtext_filter)
        vf_parts += scale_filters
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{start:.3f}",
            "-i", str(mkv_path),
            "-t", f"{duration:.3f}",
            "-map", "0:v:0", "-map", "0:a:0",
            "-vf", ",".join(vf_parts),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", str(TARGET_CRF),
            "-c:a", "aac", "-b:a", "192k", "-ar", str(TARGET_AR), "-ac", "2",
            "-avoid_negative_ts", "make_zero",
            str(out_path),
        ]
    else:
        # Pad path: trim source to `duration` in the filter graph, then
        # extend video with tpad (clone last frame) and splice in silence
        # from an anullsrc input. apad was hanging in this ffmpeg build, so
        # we use the explicit concat-of-silence pattern instead.
        v_chain = [
            f"trim=0:{duration:.3f}", "setpts=PTS-STARTPTS",
            f"fps={TARGET_FPS}", "setsar=1", "format=yuv420p",
        ]
        if drawtext_filter:
            v_chain.append(drawtext_filter)
        v_chain += scale_filters
        v_chain.append(f"tpad=stop_mode=clone:stop_duration={pad:.3f}")
        v_part = "[0:v]" + ",".join(v_chain) + "[vout]"
        a_part = (
            f"[0:a]atrim=0:{duration:.3f},asetpts=PTS-STARTPTS[a0];"
            f"[1:a]atrim=0:{pad:.3f},asetpts=PTS-STARTPTS[a1];"
            f"[a0][a1]concat=n=2:v=0:a=1[aout]"
        )
        filter_complex = v_part + ";" + a_part
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{start:.3f}",
            "-i", str(mkv_path),
            "-f", "lavfi", "-i",
            f"anullsrc=channel_layout=stereo:sample_rate={TARGET_AR}",
            "-filter_complex", filter_complex,
            "-map", "[vout]", "-map", "[aout]",
            "-t", f"{duration + pad:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", str(TARGET_CRF),
            "-c:a", "aac", "-b:a", "192k", "-ar", str(TARGET_AR), "-ac", "2",
            "-avoid_negative_ts", "make_zero",
            str(out_path),
        ]

    result = subprocess.run(cmd, capture_output=True)
    if caption_path is not None:
        caption_path.unlink(missing_ok=True)
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")
        raise subprocess.CalledProcessError(
            result.returncode, cmd, output=result.stdout, stderr=stderr.encode()
        )


def concat_segments(concat_list_path, output_path):
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_list_path),
        "-c", "copy",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def gap_extend_clip(src_path, out_path, pad):
    """Append `pad` s of held last frame + silence to an existing clip.

    Used for pre-cut clips (already subtitled, locked params) so a between-word
    gap can be added without going back to the source mkv. Uses anullsrc+concat
    for the silence (apad hangs in this ffmpeg build).
    """
    fc = (
        f"[0:v]tpad=stop_mode=clone:stop_duration={pad:.3f},format=yuv420p[vout];"
        f"[0:a]asetpts=PTS-STARTPTS[a0];"
        f"[1:a]atrim=0:{pad:.3f},asetpts=PTS-STARTPTS[a1];"
        f"[a0][a1]concat=n=2:v=0:a=1[aout]"
    )
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(src_path),
        "-f", "lavfi", "-i",
        f"anullsrc=channel_layout=stereo:sample_rate={TARGET_AR}",
        "-filter_complex", fc,
        "-map", "[vout]", "-map", "[aout]",
        "-r", str(TARGET_FPS),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(TARGET_CRF),
        "-c:a", "aac", "-b:a", "192k", "-ar", str(TARGET_AR), "-ac", "2",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")
        raise subprocess.CalledProcessError(
            result.returncode, cmd, output=result.stdout, stderr=stderr.encode())


def load_precut_index(clips_dir):
    """Load the pre-cut manifest if present: (index, words) or (None, None).

    index = {word: [{"file","stem","start","end","conf","quality"}, ...]}.
    """
    path = Path(clips_dir) / "index.json"
    if not path.exists():
        return None, None
    data = json.load(open(path, encoding="utf-8"))
    return data, sorted(data.keys())


def build_video(selected_words, index, output_dir, progress=None,
                burn_subs=BURN_SUBS_DEFAULT, gap=GAP_DEFAULT_SEC,
                clips_dir=None, mkv_dir=MKV_DIR):
    """Pick a clip per word (conf-weighted), assemble, concat. Return Path.

    Per word the picked occurrence is either a pre-cut clip (`file` key, taken
    from clips_dir) or extracted live from the source mkv. `gap` (seconds) adds
    a held-frame + silence after every word except the last.
    """
    if not selected_words:
        raise ValueError("nothing selected")

    picks = []
    for w in selected_words:
        occs = index.get(w.lower())
        if not occs:
            raise ValueError(f"word not in index: {w}")
        # Verbose index (wordClips / live): confidence-weighted pick.
        # Slim index (lineClips): position-weighted pick — list is pre-sorted
        # best-first, weight = 1/(i+1) so lower-quality clips can be chosen
        # but with decreasing probability.
        if "conf" in occs[0]:
            picks.append((w, weighted_pick(occs, random)))
        else:
            weights = [1 / (i + 1) ** 0.3 for i in range(len(occs))]
            picks.append((w, random.choices(occs, weights=weights, k=1)[0]))

    output_path = unique_output_path(output_dir, sanitize_filename(selected_words))
    gap = max(0.0, float(gap))

    with tempfile.TemporaryDirectory(prefix="wordvid_") as tmp:
        tmp = Path(tmp)
        for i, (word, occ) in enumerate(picks):
            if progress:
                progress(f"preparing {i + 1}/{len(picks)}: {word}")
            pad_after = gap if i < len(picks) - 1 else 0.0
            seg = tmp / f"seg_{i:04d}.mp4"

            if "start_in_clip" in occ:  # line clip: extract n-gram sub-segment
                src = Path(clips_dir) / occ["file"]
                extract_segment(
                    src,
                    max(0.0, occ["start_in_clip"] - PAD_SEC),
                    occ["end_in_clip"] + PAD_SEC,
                    seg, pad_after=pad_after, scale_to=None)
            elif "file" in occ:  # wordClips: copy or gap-extend the pre-cut clip
                src = Path(clips_dir) / occ["file"]
                if pad_after > 0:
                    gap_extend_clip(src, seg, pad_after)
                else:
                    shutil.copyfile(src, seg)  # instant; locked params already
            else:              # extract live from the mkv
                mkv = Path(mkv_dir) / f"{occ['stem']}.mkv"
                extract_segment(
                    mkv, max(0.0, occ["start"] - PAD_SEC), occ["end"] + PAD_SEC,
                    seg, caption=word if burn_subs else None, pad_after=pad_after,
                    scale_to=(CLIP_W, CLIP_H))

        concat_list = tmp / "concat.txt"
        with open(concat_list, "w", encoding="utf-8") as f:
            for i in range(len(picks)):
                f.write(f"file 'seg_{i:04d}.mp4'\n")

        if progress:
            progress(f"concatenating {len(picks)} segment(s)…")
        concat_segments(concat_list, output_path)

    return output_path


# --- UI -----------------------------------------------------------------------

class NgramPickerApp:
    def __init__(self, root, index, ngrams, clips_dir=None):
        self.root = root
        self.index = index
        self.ngrams = ngrams
        self.clips_dir = clips_dir   # set => use pre-cut clips, else live mkv
        self.selected = []

        root.title("Seinfeld Ngram Mashup")
        root.geometry("520x540")

        # Input row
        top = ttk.Frame(root, padding=10)
        top.pack(fill="x")

        ttk.Label(top, text="N-gram:").pack(side="left")
        self.entry = ttk.Entry(top)
        self.entry.pack(side="left", fill="x", expand=True, padx=(6, 6))
        self.entry.bind("<KeyRelease>", self._on_keyrelease)
        self.entry.bind("<Return>", lambda e: self._on_add())
        self.entry.bind("<Down>", self._focus_suggestions)

        self.add_button = ttk.Button(top, text="Add", command=self._on_add)
        self.add_button.pack(side="left")

        # Suggestions
        sug_frame = ttk.Frame(root, padding=(10, 0))
        sug_frame.pack(fill="x")
        ttk.Label(sug_frame, text="Suggestions:").pack(anchor="w")
        self.suggestions = tk.Listbox(sug_frame, height=MAX_HITS,
                                      exportselection=False)
        self.suggestions.pack(fill="x", pady=(2, 8))
        self.suggestions.bind("<Double-Button-1>", lambda e: self._add_from_suggestions())
        self.suggestions.bind("<Return>", lambda e: self._add_from_suggestions())
        self.suggestions.bind("<<ListboxSelect>>", self._on_suggestion_pick)

        # Selected words
        sel_frame = ttk.Frame(root, padding=10)
        sel_frame.pack(fill="both", expand=True)
        ttk.Label(sel_frame, text="Selected n-grams (in order):").pack(anchor="w")
        list_with_scroll = ttk.Frame(sel_frame)
        list_with_scroll.pack(fill="both", expand=True, pady=(2, 0))
        self.selected_box = tk.Listbox(list_with_scroll, exportselection=False)
        self.selected_box.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(list_with_scroll, command=self.selected_box.yview)
        sb.pack(side="right", fill="y")
        self.selected_box.config(yscrollcommand=sb.set)

        # Action row
        action = ttk.Frame(root, padding=10)
        action.pack(fill="x")
        self.build_button = ttk.Button(action, text="Build Video",
                                       command=self._on_build)
        self.build_button.pack(side="left")
        self.clear_button = ttk.Button(action, text="Clear",
                                       command=self._on_clear)
        self.clear_button.pack(side="left", padx=(6, 0))

        # Gap slider with live ms readout.
        gap_frame = ttk.Frame(action)
        gap_frame.pack(side="right", padx=(0, 12))
        self.gap_var = tk.DoubleVar(value=GAP_DEFAULT_SEC)
        self.gap_label_var = tk.StringVar(
            value=f"Gap: {GAP_DEFAULT_SEC * 1000:.0f} ms")
        ttk.Label(gap_frame, textvariable=self.gap_label_var).pack(anchor="e")
        ttk.Scale(
            gap_frame, from_=0.0, to=GAP_MAX_SEC, orient="horizontal",
            variable=self.gap_var, length=160,
            command=lambda v: self.gap_label_var.set(
                f"Gap: {float(v) * 1000:.0f} ms"),
        ).pack()

        # Status
        self.status_var = tk.StringVar(value=f"ready — {len(self.ngrams)} n-grams indexed")
        ttk.Label(root, textvariable=self.status_var, anchor="w",
                  padding=(10, 4)).pack(fill="x", side="bottom")

        self._refresh_suggestions("")

    # ----- suggestion handling -----

    def _on_keyrelease(self, _event):
        self._refresh_suggestions(self.entry.get())

    def _refresh_suggestions(self, prefix):
        prefix = prefix.lower().strip()
        self.suggestions.delete(0, tk.END)
        if not prefix:
            hits = self.ngrams[:MAX_HITS]
        else:
            hits = [ng for ng in self.ngrams if ng.startswith(prefix)][:MAX_HITS]
        for ng in hits:
            self.suggestions.insert(tk.END, ng)

    def _focus_suggestions(self, _event):
        if self.suggestions.size():
            self.suggestions.focus_set()
            self.suggestions.selection_clear(0, tk.END)
            self.suggestions.selection_set(0)

    def _on_suggestion_pick(self, _event):
        sel = self.suggestions.curselection()
        if not sel:
            return
        ng = self.suggestions.get(sel[0])
        self.entry.delete(0, tk.END)
        self.entry.insert(0, ng)

    def _add_from_suggestions(self):
        sel = self.suggestions.curselection()
        if not sel:
            return
        ng = self.suggestions.get(sel[0])
        self._add_ngram(ng)

    # ----- selected list -----

    def _on_add(self):
        ng = self.entry.get().strip().lower()
        if not ng:
            return
        self._add_ngram(ng)

    def _add_ngram(self, ng):
        ng = ng.lower().strip()
        if ng not in self.index:
            self._set_status(f"unknown n-gram: {ng}")
            return
        pool = self.index[ng]
        # Slim lineClips index has no quality field; treat all as usable.
        low = "quality" in pool[0] and word_quality(pool) == "low"
        self.selected.append(ng)
        self.selected_box.insert(tk.END, f"{ng}  [low quality]" if low else ng)
        self.selected_box.see(tk.END)
        self.entry.delete(0, tk.END)
        self._refresh_suggestions("")
        note = " — low quality (only weak clips)" if low else ""
        self._set_status(f"added '{ng}' ({len(self.selected)} selected){note}")
        self.entry.focus_set()

    def _on_clear(self):
        self.selected.clear()
        self.selected_box.delete(0, tk.END)
        self._set_status("cleared")

    # ----- build -----

    def _on_build(self):
        if not self.selected:
            self._set_status("nothing selected")
            return
        ngrams_snapshot = list(self.selected)
        gap = float(self.gap_var.get())
        self._toggle_buttons(False)
        self._set_status("building…")

        def worker():
            try:
                out_path = build_video(
                    ngrams_snapshot, self.index, OUTPUT_DIR,
                    progress=lambda msg: self._post_status(msg),
                    gap=gap,
                    clips_dir=self.clips_dir,
                    mkv_dir=MKV_DIR,
                )
                self._post_status(f"done → {out_path.name}")
            except subprocess.CalledProcessError as e:
                stderr = (e.stderr or b"").decode("utf-8", errors="replace")
                tail = "\n".join(stderr.strip().splitlines()[-3:]) or "(no stderr)"
                self._post_status(f"ffmpeg failed: {tail}")
            except Exception as e:
                self._post_status(f"error: {e}")
            finally:
                self.root.after(0, lambda: self._toggle_buttons(True))

        threading.Thread(target=worker, daemon=True).start()

    def _toggle_buttons(self, enabled):
        state = "normal" if enabled else "disabled"
        for b in (self.build_button, self.clear_button, self.add_button):
            b.config(state=state)

    def _set_status(self, msg):
        self.status_var.set(msg)

    def _post_status(self, msg):
        self.root.after(0, lambda: self._set_status(msg))


# --- Main ---------------------------------------------------------------------

def main():
    # Priority: lineClips (all n-grams) > wordClips (words only) > live MKV.
    line_index, _ = load_precut_index(LINE_CLIPS_DIR)
    if line_index:
        index = line_index
        ngrams = sorted(index.keys())
        clips_dir = LINE_CLIPS_DIR
        print(f"using line clips: {len(ngrams)} n-grams from {LINE_CLIPS_DIR}")
    else:
        index, ngrams = load_precut_index(CLIPS_DIR)
        clips_dir = CLIPS_DIR
        if not ngrams:
            index, ngrams = build_candidate_index(WORDSCORES_DIR)
            clips_dir = None
            print(f"no pre-cut clips; live mkv mode: {len(ngrams)} words "
                  f"from {WORDSCORES_DIR}")
        else:
            print(f"using pre-cut word clips: {len(ngrams)} words from {CLIPS_DIR}")
    if not ngrams:
        print("No n-grams indexed; aborting.", file=sys.stderr)
        sys.exit(1)
    root = tk.Tk()
    NgramPickerApp(root, index, ngrams, clips_dir=clips_dir)
    root.mainloop()


if __name__ == "__main__":
    main()
