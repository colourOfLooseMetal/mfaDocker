// UI: autocomplete word picker -> client-side ffmpeg.wasm mashup.
import { getFFmpeg, joinClips } from "./ffmpeg.js";
import { loadClipIndex, wordQuality, weightedPick } from "./clipIndex.js";

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
let WORDS = [];
const selected = []; // array of chosen words (in order)

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
    const { index, words } = await loadClipIndex();
    INDEX = index;
    WORDS = words;
    els.status.textContent = `${words.length} words available`;
    refreshSuggestions("");
  } catch (e) {
    els.status.textContent = e.message;
  }
}

// ---- autocomplete ----
function refreshSuggestions(prefix) {
  prefix = prefix.toLowerCase().trim();
  const hits = (prefix
    ? WORDS.filter((w) => w.startsWith(prefix))
    : WORDS).slice(0, MAX_HITS);
  els.suggestions.innerHTML = "";
  for (const w of hits) {
    const li = document.createElement("li");
    li.textContent = w;
    if (wordQuality(INDEX[w]) === "low") {
      li.classList.add("low");
      li.textContent += "  (low quality)";
    }
    li.addEventListener("click", () => addWord(w));
    els.suggestions.appendChild(li);
  }
}

// ---- selected list ----
function addWord(word) {
  word = word.toLowerCase().trim();
  if (!word) return;
  if (!INDEX[word]) {
    els.status.textContent = `unknown word: ${word}`;
    return;
  }
  selected.push(word);
  renderSelected();
  els.input.value = "";
  refreshSuggestions("");
  els.input.focus();
}

function renderSelected() {
  els.selected.innerHTML = "";
  selected.forEach((w, i) => {
    const li = document.createElement("li");
    const low = wordQuality(INDEX[w]) === "low";
    li.textContent = low ? `${w}  [low quality]` : w;
    if (low) li.classList.add("low");
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
  const words = [...selected];
  const gap = parseFloat(els.gap.value);
  setBusy(true);
  els.status.textContent = "building…";
  try {
    const urls = words.map((w) => weightedPick(INDEX[w]).url);
    const blob = await joinClips(urls, {
      gap,
      onProgress: (p) => {
        if (p > 0 && p <= 1) els.status.textContent = `building… ${Math.round(p * 100)}%`;
      },
    });
    const url = URL.createObjectURL(blob);
    els.preview.src = url;
    els.download.href = url;
    els.download.download = filenameFor(words);
    els.result.hidden = false;
    els.status.textContent = `done — ${(blob.size / 1e6).toFixed(2)}MB`;
  } catch (e) {
    els.status.textContent = `build failed: ${e.message}`;
  } finally {
    setBusy(false);
  }
}

function filenameFor(words) {
  const base = words.join("_").toLowerCase().replace(/[^a-z0-9_]/g, "");
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
    if (e.key === "Enter") { addWord(els.input.value); return; }
    refreshSuggestions(els.input.value);
  });
  els.add.addEventListener("click", () => addWord(els.input.value));
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
