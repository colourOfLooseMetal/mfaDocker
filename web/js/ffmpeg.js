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

// Join an ordered list of clip URLs into one mp4 Blob.
//   mode:  "copy" (uniform clips, fast) | "encode" (fallback, slow)
//   gap:   seconds of held-frame + silence after every clip except the last
export async function joinClips(urls, { mode = "copy", gap = 0,
                                        onLog, onProgress } = {}) {
  const ff = await getFFmpeg();
  if (onLog) ff.on("log", ({ message }) => onLog(message));
  if (onProgress) ff.on("progress", ({ progress }) => onProgress(progress));

  const { fetchFile } = window.FFmpegUtil;

  for (let i = 0; i < urls.length; i++) {
    await ff.writeFile(`c${i}.mp4`, await fetchFile(urls[i]));
  }
  try { await ff.deleteFile("out.mp4"); } catch {}

  if (mode === "encode") {
    // Re-encode concat: tolerates any mismatch (no gap support here).
    const args = [];
    urls.forEach((_, i) => args.push("-i", `c${i}.mp4`));
    const streams = urls.map((_, i) => `[${i}:v][${i}:a]`).join("");
    args.push(
      "-filter_complex", `${streams}concat=n=${urls.length}:v=1:a=1[v][a]`,
      "-map", "[v]", "-map", "[a]",
      "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
      "-c:a", "aac", "-movflags", "+faststart", "out.mp4");
    await ff.exec(args);
  } else {
    // Copy concat. Add per-clip held-frame gaps first if requested.
    const segs = [];
    for (let i = 0; i < urls.length; i++) {
      if (gap > 0 && i < urls.length - 1) {
        await ff.exec(gapExtendArgs(`c${i}.mp4`, `g${i}.mp4`, gap));
        segs.push(`g${i}.mp4`);
      } else {
        segs.push(`c${i}.mp4`);
      }
    }
    const list = segs.map((s) => `file '${s}'`).join("\n");
    await ff.writeFile("list.txt", new TextEncoder().encode(list));
    await ff.exec([
      "-f", "concat", "-safe", "0", "-i", "list.txt",
      "-c", "copy", "-movflags", "+faststart", "out.mp4",
    ]);
  }

  const data = await ff.readFile("out.mp4");
  if (!data?.length) throw new Error("ffmpeg produced empty output");
  return new Blob([data.buffer], { type: "video/mp4" });
}
