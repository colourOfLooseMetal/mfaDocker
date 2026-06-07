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
const PAD_SEC = 0;    // no padding beyond the n-gram's documented timing

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

// Tear down the engine so the next getFFmpeg() reloads a fresh worker. A run
// that aborts the wasm runtime leaves the worker dead; without a reset every
// later build would fail too, turning one rare failure into "broken until
// reload" and muddying which combo actually broke.
export function resetFFmpeg() {
  try { instance?.terminate(); } catch {}
  instance = null;
  loading = null;
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

// Build a descriptive Error for a failed ffmpeg step. The worker rejects EXEC
// with a bare *string* (its own `e.toString()`), which is exactly why the old
// `catch (e) => e.message` surfaced "undefined". Normalize that here and staple
// on the recent ffmpeg stderr (the real reason) plus the args, so callers can
// log a true diagnostic and the on-screen message is meaningful.
function ffmpegStepError(label, args, cause, logTail) {
  const causeMsg =
    cause == null ? "" : (cause.message || (typeof cause === "string" ? cause : String(cause)));
  const err = new Error(causeMsg ? `${label}: ${causeMsg}` : label);
  err.ffmpegArgs = args || null;
  err.ffmpegLog = (logTail || []).join("\n");
  if (cause != null) err.cause = cause;
  return err;
}

// Tag a thrown value with the segment (line clip) that caused it, so the caller
// can exclude that specific clip from the next retry. Returns the value so it
// can be thrown inline.
function withSegment(err, seg) {
  if (err && typeof err === "object") err.segment = seg;
  return err;
}

// Heuristic: did this rejection abort the wasm runtime (worker now dead)? Then
// the engine must be reset before the next build can succeed.
function isFatalAbort(cause) {
  const s = cause == null ? "" : (cause.message || String(cause));
  return /abort|runtimeerror|out of bounds|unreachable|memory/i.test(s);
}

// Build one mp4 Blob from an ordered list of n-gram sub-segments. Each segment
// is { url, start, end }: the line-clip URL and the n-gram's in-clip bounds.
// We fetch each distinct line clip once, trim every segment out of it, then
// concat-copy. `gap` adds held-frame + silence after each segment but the last.
export async function joinSegments(segments, { gap = 0, onLog, onProgress } = {}) {
  const ff = await getFFmpeg();

  // Keep the last ~120 ffmpeg log lines. On failure these stderr lines are the
  // real explanation; the rejected promise itself carries only a terse string.
  // Registered per-call and removed in finally so listeners don't stack on the
  // singleton across builds.
  const logTail = [];
  const onLogEvt = ({ message }) => {
    logTail.push(message);
    if (logTail.length > 120) logTail.shift();
    onLog?.(message);
  };
  const onProgEvt = onProgress ? ({ progress }) => onProgress(progress) : null;
  ff.on("log", onLogEvt);
  if (onProgEvt) ff.on("progress", onProgEvt);

  // Run one ffmpeg command, turning both worker rejections (wasm crash) and
  // non-zero exit codes into a labelled, log-bearing Error. exec() resolves
  // with the return code and does NOT throw on a non-zero exit, so a failed
  // command would otherwise pass silently until a later step broke.
  const run = async (label, args) => {
    let ret;
    try {
      ret = await ff.exec(args);
    } catch (cause) {
      if (isFatalAbort(cause)) resetFFmpeg();
      throw ffmpegStepError(`ffmpeg ${label} crashed`, args, cause, logTail);
    }
    if (ret !== 0) throw ffmpegStepError(`ffmpeg ${label} exited ${ret}`, args, null, logTail);
  };

  try {
    const { fetchFile } = window.FFmpegUtil;

    // Fetch each distinct line clip into the FS once (a clip can back several
    // n-grams). Map url -> FS filename.
    const srcFor = new Map();
    let n = 0;
    for (const seg of segments) {
      if (!srcFor.has(seg.url)) {
        const name = `src${n++}.mp4`;
        let bytes;
        try {
          bytes = await fetchFile(seg.url);
        } catch (cause) {
          throw withSegment(
            new Error(`failed to download clip ${seg.url}: ${cause?.message || cause}`), seg);
        }
        if (!bytes?.length) throw withSegment(new Error(`downloaded empty clip ${seg.url}`), seg);
        await ff.writeFile(name, bytes);
        srcFor.set(seg.url, name);
      }
    }

    try { await ff.deleteFile("out.mp4"); } catch {}

    // Trim each segment, then optionally gap-extend all but the last. Tag any
    // failure with the offending segment so the caller can exclude that clip
    // and retry with a different one.
    const segNames = [];
    for (let i = 0; i < segments.length; i++) {
      const seg = segments[i];
      const segName = `seg${i}.mp4`;
      try {
        await run(
          `trim seg ${i} (${seg.url} @ ${seg.start}-${seg.end})`,
          trimArgs(srcFor.get(seg.url), segName, seg.start, seg.end));
        if (gap > 0 && i < segments.length - 1) {
          const gName = `g${i}.mp4`;
          await run(`gap-extend seg ${i}`, gapExtendArgs(segName, gName, gap));
          segNames.push(gName);
        } else {
          segNames.push(segName);
        }
      } catch (e) {
        throw withSegment(e, seg);
      }
    }

    const list = segNames.map((s) => `file '${s}'`).join("\n");
    await ff.writeFile("list.txt", new TextEncoder().encode(list));
    await run("concat", [
      "-f", "concat", "-safe", "0", "-i", "list.txt",
      "-c", "copy", "-movflags", "+faststart", "out.mp4",
    ]);

    const data = await ff.readFile("out.mp4");
    if (!data?.length) throw ffmpegStepError("ffmpeg produced empty output", null, null, logTail);
    return new Blob([data.buffer], { type: "video/mp4" });
  } finally {
    ff.off("log", onLogEvt);
    if (onProgEvt) ff.off("progress", onProgEvt);
  }
}
