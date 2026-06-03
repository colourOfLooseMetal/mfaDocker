// ffmpeg.wasm engine + clip joining.
//
// Uses the UMD builds loaded as globals in index.html
// (window.FFmpegWASM / window.FFmpegUtil) so there is no bundler/import-map
// step. Single-thread core => no SharedArrayBuffer => no special COOP/COEP
// headers needed.

// Engine files are vendored under web/ffmpeg/ and served same-origin (no CDN /
// external network). Resolve absolute URLs against the page so toBlobURL works
// regardless of mount path.
const BASE = new URL("ffmpeg/", document.baseURI).href;  // .../web/ffmpeg/

const AR = 48000;
const FPS = 30;
const CRF = 21;       // matches buildNgramVideo.TARGET_CRF
const PAD_SEC = 0.02; // matches buildNgramVideo.PAD_SEC (each side of the n-gram)

let instance = null;
let loading = null;

// Lazy singleton: load the ~31MB engine once, reuse for every join.
export async function getFFmpeg(onProgress) {
  if (instance) return instance;
  if (loading) return loading;

  const { FFmpeg } = window.FFmpegWASM;
  const { toBlobURL } = window.FFmpegUtil;

  loading = (async () => {
    const ff = new FFmpeg();
    onProgress?.();
    // Notes from the wiring:
    //  - Plain 2-arg toBlobURL only; the progress-callback variant can
    //    double-read the response ("body stream already read").
    //  - Do NOT pass classWorkerURL: in 0.12.15 that spawns a *module* worker
    //    against a hardcoded file:// base (the worker uses importScripts and
    //    breaks). Omitting it auto-locates the correct same-origin *classic*
    //    worker (814.ffmpeg.js) next to the vendored main script.
    await ff.load({
      coreURL: await toBlobURL(`${BASE}ffmpeg-core.js`, "text/javascript"),
      wasmURL: await toBlobURL(`${BASE}ffmpeg-core.wasm`, "application/wasm"),
    });
    instance = ff;
    return ff;
  })();

  return loading;
}

function gapExtendArgs(inName, outName, pad) {
  const p = pad.toFixed(3);
  const fc =
    `[0:v]tpad=stop_mode=clone:stop_duration=${p},format=yuv420p[v];` +
    `[0:a]asetpts=PTS-STARTPTS[a0];` +
    `[1:a]atrim=0:${p},asetpts=PTS-STARTPTS[a1];` +
    `[a0][a1]concat=n=2:v=0:a=1[a]`;
  return [
    "-i", inName,
    "-f", "lavfi", "-i", `anullsrc=channel_layout=stereo:sample_rate=${AR}`,
    "-filter_complex", fc,
    "-map", "[v]", "-map", "[a]",
    "-r", String(FPS),
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
    "-c:a", "aac", "-b:a", "192k", "-ar", String(AR), "-ac", "2",
    outName,
  ];
}

// Trim+re-encode one n-gram sub-segment [start-PAD, end+PAD] out of a line
// clip already loaded in the FS as `inName`. Mirrors extract_segment's no-pad
// branch in buildNgramVideo.py: fast -ss seek, locked encode params (so the
// segments concat-copy), no scale (line clips are already 640x360). Output PTS
// start at 0 via -avoid_negative_ts make_zero.
function trimArgs(inName, outName, start, end) {
  const ss = Math.max(0, start - PAD_SEC);
  const dur = Math.max(end + PAD_SEC - ss, 0.01);
  return [
    "-ss", ss.toFixed(3),
    "-i", inName,
    "-t", dur.toFixed(3),
    "-map", "0:v:0", "-map", "0:a:0",
    "-vf", `fps=${FPS},setsar=1,format=yuv420p`,
    "-c:v", "libx264", "-preset", "veryfast", "-crf", String(CRF),
    "-c:a", "aac", "-b:a", "192k", "-ar", String(AR), "-ac", "2",
    "-avoid_negative_ts", "make_zero",
    outName,
  ];
}

// Build one mp4 Blob from an ordered list of n-gram sub-segments. Each segment
// is { url, start, end }: the line-clip URL and the n-gram's in-clip bounds.
// We fetch each distinct line clip once, trim every segment out of it, then
// concat-copy. `gap` adds held-frame + silence after each segment but the last.
export async function joinSegments(segments, { gap = 0, onLog, onProgress } = {}) {
  const ff = await getFFmpeg();
  if (onLog) ff.on("log", ({ message }) => onLog(message));
  if (onProgress) ff.on("progress", ({ progress }) => onProgress(progress));

  const { fetchFile } = window.FFmpegUtil;

  // Fetch each distinct line clip into the FS once (a clip can back several
  // n-grams). Map url -> FS filename.
  const srcFor = new Map();
  let n = 0;
  for (const seg of segments) {
    if (!srcFor.has(seg.url)) {
      const name = `src${n++}.mp4`;
      await ff.writeFile(name, await fetchFile(seg.url));
      srcFor.set(seg.url, name);
    }
  }

  try { await ff.deleteFile("out.mp4"); } catch {}

  // Trim each segment, then optionally gap-extend all but the last.
  const segNames = [];
  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i];
    const segName = `seg${i}.mp4`;
    await ff.exec(trimArgs(srcFor.get(seg.url), segName, seg.start, seg.end));
    if (gap > 0 && i < segments.length - 1) {
      const gName = `g${i}.mp4`;
      await ff.exec(gapExtendArgs(segName, gName, gap));
      segNames.push(gName);
    } else {
      segNames.push(segName);
    }
  }

  const list = segNames.map((s) => `file '${s}'`).join("\n");
  await ff.writeFile("list.txt", new TextEncoder().encode(list));
  await ff.exec([
    "-f", "concat", "-safe", "0", "-i", "list.txt",
    "-c", "copy", "-movflags", "+faststart", "out.mp4",
  ]);

  const data = await ff.readFile("out.mp4");
  if (!data?.length) throw new Error("ffmpeg produced empty output");
  return new Blob([data.buffer], { type: "video/mp4" });
}
