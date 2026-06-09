// UI: autocomplete n-gram picker -> client-side ffmpeg.wasm mashup.
import { getFFmpeg, joinSegments } from "./ffmpeg.js";
import { loadClipIndex, positionWeightedPick } from "./clipIndex.js";

const MAX_HITS = 8;

const $ = (id) => document.getElementById(id);
const els = {
  engine: $("engine-status"),
  input: $("word-input"),
  suggestions: $("suggestions"),
  add: $("add-btn"),
  selected: $("selected"),
  clear: $("clear-btn"),
  gap: $("gap"),
  gapLabel: $("gap-label"),
  gapPresets: document.querySelectorAll(".preset-btn"),
  build: $("build-btn"),
  status: $("status"),
  preview: $("preview"),
  download: $("download"),
  result: $("result"),
};

let INDEX = {};
let NGRAMS = [];
// Ordered list of items the phrase is built from. Each item is either
// { kind: "ngram", text } or { kind: "gap", sec } — a custom-pause token
// inserted between two n-grams that overrides the default slider gap for
// just that boundary.
const selected = [];

// Clips that crashed ffmpeg.wasm (RuntimeError / memory access out of bounds).
// WASM crashes are deterministic — the same clip will always crash. Unlike
// per-build `failed`, this persists for the whole session so bad clips are
// never retried even across separate builds.
const permanentlyBad = new Set();

function isWasmCrash(e) {
  const msg = (e?.cause?.message || e?.message || String(e || "")).toLowerCase();
  return /runtimeerror|out of bounds|unreachable|memory access|abort/.test(msg);
}

// ---- engine preload (cache the 31MB before first build) ----
async function preloadEngine() {
  try {
    els.engine.textContent = "loading...";
    await getFFmpeg();
    els.engine.textContent = "Ready!";
  } catch (e) {
    els.engine.textContent = `engine failed to load: ${e.message}`;
  }
}

// ---- clip index ----
async function loadIndex() {
  try {
    const { index, ngrams } = await loadClipIndex();
    INDEX = index;
    NGRAMS = ngrams;
    els.status.textContent = `${ngrams.length} words/phrases available`;
    refreshSuggestions("");
  } catch (e) {
    els.status.textContent = e.message;
  }
}

// ---- autocomplete ----
function refreshSuggestions(prefix) {
  prefix = prefix.toLowerCase().trim();
  const hits = (prefix
    ? NGRAMS.filter((ng) => ng.startsWith(prefix))
    : NGRAMS).slice(0, MAX_HITS);
  els.suggestions.innerHTML = "";
  for (const ng of hits) {
    const li = document.createElement("li");
    li.textContent = ng;
    li.addEventListener("click", () => addNgram(ng));
    els.suggestions.appendChild(li);
  }
}

// ---- selected list ----
function addNgram(ngram) {
  ngram = ngram.toLowerCase().trim();
  if (!ngram) return;
  if (!INDEX[ngram]) {
    els.status.textContent = `unknown word/phrase: ${ngram}`;
    return;
  }
  selected.push({ kind: "ngram", text: ngram });
  renderSelected();
  els.input.value = "";
  refreshSuggestions("");
  els.input.focus();
}

// Append a custom-pause token. It only takes effect once it sits between two
// n-grams (see planSegments) — a leading/trailing/duplicate token is simply
// ignored at build time, so we don't need to validate placement here.
function addGapToken(sec) {
  selected.push({ kind: "gap", sec });
  renderSelected();
}

function renderSelected() {
  els.selected.innerHTML = "";
  selected.forEach((item, i) => {
    const li = document.createElement("li");
    if (item.kind === "gap") {
      li.className = "gap-token";
      li.textContent = `⏸ ${Math.round(item.sec * 1000)} ms`;
    } else {
      li.textContent = item.text;
    }
    const x = document.createElement("button");
    x.textContent = "×";
    x.className = "rm";
    x.title = "remove";
    x.addEventListener("click", () => { selected.splice(i, 1); renderSelected(); });
    li.appendChild(x);
    els.selected.appendChild(li);
  });
  els.build.disabled = !selected.some((item) => item.kind === "ngram");
}

// Walk the ordered selection, splitting it into the n-gram list ffmpeg needs
// plus a per-boundary gap (seconds) to apply between each consecutive pair.
// A gap token sets the boundary immediately following it; tokens with no
// n-gram on both sides (leading, trailing, back-to-back) are dropped. Any
// boundary without a token falls back to the slider's default gap.
function planSegments(items, defaultGap) {
  const ngrams = [];
  const gaps = [];
  let pending = null;
  for (const item of items) {
    if (item.kind === "gap") {
      pending = item.sec;
      continue;
    }
    if (ngrams.length > 0) gaps.push(pending != null ? pending : defaultGap);
    ngrams.push(item.text);
    pending = null;
  }
  return { ngrams, gaps };
}

// ---- build ----
async function build() {
  const defaultGap = parseFloat(els.gap.value);
  const { ngrams, gaps } = planSegments(selected, defaultGap);
  if (!ngrams.length) return;
  setBusy(true);
  els.status.textContent = "building…";
  try {
    const blob = await buildWithRetry(ngrams, gaps);
    const url = URL.createObjectURL(blob);
    els.preview.src = url;
    els.download.href = url;
    els.download.download = filenameFor(ngrams);
    els.result.hidden = false;
    els.status.textContent = `done — ${(blob.size / 1e6).toFixed(2)}MB`;
  } catch (e) {
    // Always dump the real failure to the console: the full error object plus
    // the ffmpeg stderr / args that joinSegments staples on (e.ffmpegLog is the
    // actual reason a step crashed). The on-screen text uses errorText() so a
    // thrown non-Error can never degrade to "build failed: undefined".
    console.error("[build] failed:", e);
    if (e && e.ffmpegArgs) console.error("[build] ffmpeg args:", e.ffmpegArgs.join(" "));
    if (e && e.ffmpegLog) console.error("[build] ffmpeg log tail:\n" + e.ffmpegLog);
    els.status.textContent = `build failed: ${errorText(e)}`;
  } finally {
    setBusy(false);
  }
}

// Builds are flaky on a *per-clip* basis: a handful of line clips fail to
// trim/concat (e.g. a clip with no audio stream, or trim bounds at the very
// edge), and positionWeightedPick only lands on them some of the time — hence
// "fails now, works on refresh". Rather than make the user retry by hand, we
// re-pick fresh clips and rebuild, up to a cap. Each failed attempt is logged
// with its segments so the offending clip URL is captured even when a later
// attempt succeeds (send those to fix/exclude the source clip).
const MAX_BUILD_ATTEMPTS = 3;

async function buildWithRetry(ngrams, gaps) {
  const failed = new Set(); // clip urls proven bad this build; never re-picked
  let lastErr;
  for (let attempt = 1; attempt <= MAX_BUILD_ATTEMPTS; attempt++) {
    const segments = ngrams.map((ng) => pickSegment(ng, failed));
    console.info(`[build] attempt ${attempt}/${MAX_BUILD_ATTEMPTS}`, "segments:", segments);
    try {
      return await joinSegments(segments, {
        gaps,
        // ffmpeg stderr → console at the "verbose" level (hidden by default;
        // turn on "Verbose" in the console level filter to watch a build live).
        onLog: (m) => console.debug("[ffmpeg]", m),
        onProgress: (p) => {
          if (p > 0 && p <= 1) els.status.textContent = `building… ${Math.round(p * 100)}%`;
        },
      });
    } catch (e) {
      lastErr = e;
      // joinSegments tags the offending clip onto e.segment — exclude it so the
      // retry can't pick it again (positionWeightedPick favours the top clips,
      // so without this the same bad clip would often be re-chosen).
      if (e?.segment?.url) {
        failed.add(e.segment.url);
        if (isWasmCrash(e)) {
          // WASM crashes are deterministic — blacklist for the whole session.
          permanentlyBad.add(e.segment.url);
          console.error(`[build] WASM crash — permanently blacklisting: ${e.segment.url}`);
        }
      }
      // Warn (not error): a later attempt may still succeed. This names the bad
      // clip — send the url(s) here to fix/exclude the source clip.
      console.warn(`[build] attempt ${attempt} failed`,
        e?.segment?.url ? `(bad clip: ${e.segment.url})` : "", "segments:", segments, e);
      if (e && e.ffmpegLog) console.warn("[build] ffmpeg log tail:\n" + e.ffmpegLog);
      // Stop early if no different pick is possible: every n-gram is either
      // single-clip or has had all its clips fail/blacklisted. (The attempt
      // cap still bounds us regardless.)
      const allExcluded = new Set([...failed, ...permanentlyBad]);
      const canVary = ngrams.some((ng) => {
        const pool = INDEX[ng] || [];
        return pool.length > 1 && pool.some((o) => !allExcluded.has(o.url));
      });
      if (!canVary || attempt === MAX_BUILD_ATTEMPTS) break;
      els.status.textContent = `a clip failed — retrying (${attempt + 1}/${MAX_BUILD_ATTEMPTS})…`;
    }
  }
  throw lastErr;
}

// Pick a clip for an n-gram, skipping any clip already proven bad this build.
// Fails loudly with the n-gram name if its pool is missing/empty; otherwise
// positionWeightedPick would return undefined and only blow up later inside
// ffmpeg as a confusing "cannot read 'url'".
function pickSegment(ng, failed) {
  const pool = INDEX[ng];
  if (!pool || !pool.length) throw new Error(`no clips available for "${ng}"`);
  const allExcluded = (failed?.size || permanentlyBad.size)
    ? new Set([...(failed || []), ...permanentlyBad])
    : null;
  const usable = allExcluded ? pool.filter((o) => !allExcluded.has(o.url)) : pool;
  if (!usable.length) {
    // Every clip for this n-gram is blacklisted — throw now instead of
    // handing a known-bad clip to ffmpeg to crash on again.
    throw new Error(`all clips for "${ng}" are blacklisted (${pool.length} clip${pool.length === 1 ? "" : "s"} total, all failed)`);
  }
  return positionWeightedPick(usable);
}

// Render any thrown value as readable text — never "undefined". Errors carry a
// message; bare strings pass through; anything else is JSON/String-ified.
function errorText(e) {
  if (e == null) return "unknown error (see console)";
  if (typeof e === "string") return e;
  if (e.message) return e.message;
  try { return JSON.stringify(e); } catch { return String(e); }
}

function filenameFor(ngrams) {
  const base = ngrams.join("_").toLowerCase().replace(/[^a-z0-9_]/g, "");
  return `${base.slice(0, 100) || "mashup"}.mp4`;
}

function setBusy(b) {
  els.build.disabled = b || selected.length === 0;
  els.add.disabled = b;
  els.clear.disabled = b;
}

// ---- wire up ----
function init() {
  els.input.addEventListener("keyup", (e) => {
    if (e.key === "Enter") { addNgram(els.input.value); return; }
    refreshSuggestions(els.input.value);
  });
  els.add.addEventListener("click", () => addNgram(els.input.value));
  els.clear.addEventListener("click", () => {
    selected.length = 0;
    renderSelected();
    els.status.textContent = "cleared";
  });
  els.gap.addEventListener("input", () => {
    els.gapLabel.textContent = `${Math.round(els.gap.value * 1000)} ms`;
  });
  els.gapLabel.textContent = `${Math.round(els.gap.value * 1000)} ms`;

  // Preset buttons append a custom-pause token to the phrase — it overrides
  // the default slider gap for just the boundary it lands on, so a single
  // phrase can mix the default gap with one-off pauses.
  els.gapPresets.forEach((btn) => {
    btn.addEventListener("click", () => addGapToken(parseFloat(btn.dataset.gap)));
  });
  els.build.addEventListener("click", build);

  preloadEngine();
  loadIndex();
}

document.addEventListener("DOMContentLoaded", init);
