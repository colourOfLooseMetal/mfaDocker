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
  build: $("build-btn"),
  status: $("status"),
  preview: $("preview"),
  download: $("download"),
  result: $("result"),
};

let INDEX = {};
let NGRAMS = [];
const selected = []; // array of chosen n-grams (in order)

// ---- engine preload (cache the 31MB before first build) ----
async function preloadEngine() {
  try {
    els.engine.textContent = "loading video engine (~31 MB, one-time)…";
    await getFFmpeg();
    els.engine.textContent = "video engine ready ✓";
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
  selected.push(ngram);
  renderSelected();
  els.input.value = "";
  refreshSuggestions("");
  els.input.focus();
}

function renderSelected() {
  els.selected.innerHTML = "";
  selected.forEach((ng, i) => {
    const li = document.createElement("li");
    li.textContent = ng;
    const x = document.createElement("button");
    x.textContent = "×";
    x.className = "rm";
    x.title = "remove";
    x.addEventListener("click", () => { selected.splice(i, 1); renderSelected(); });
    li.appendChild(x);
    els.selected.appendChild(li);
  });
  els.build.disabled = selected.length === 0;
}

// ---- build ----
async function build() {
  if (!selected.length) return;
  const ngrams = [...selected];
  const gap = parseFloat(els.gap.value);
  setBusy(true);
  els.status.textContent = "building…";
  try {
    const segments = ngrams.map((ng) => positionWeightedPick(INDEX[ng]));
    const blob = await joinSegments(segments, {
      gap,
      onProgress: (p) => {
        if (p > 0 && p <= 1) els.status.textContent = `building… ${Math.round(p * 100)}%`;
      },
    });
    const url = URL.createObjectURL(blob);
    els.preview.src = url;
    els.download.href = url;
    els.download.download = filenameFor(ngrams);
    els.result.hidden = false;
    els.status.textContent = `done — ${(blob.size / 1e6).toFixed(2)}MB`;
  } catch (e) {
    els.status.textContent = `build failed: ${e.message}`;
  } finally {
    setBusy(false);
  }
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
  els.build.addEventListener("click", build);

  preloadEngine();
  loadIndex();
}

document.addEventListener("DOMContentLoaded", init);
