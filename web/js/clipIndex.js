// Clip index loader.
//
// ============================ INTERIM / TODO =================================
// The storage format for the web word/phrase list is NOT finalized. The web
// app must NOT re-threshold the full corpus on every load like the desktop
// tool does. Eventually a curated word/phrase -> clip-link list (built once,
// possibly including multi-word phrases) will be the source here.
//
// For now this reads the cutter's manifest (wordClips/index.json) so the app
// is demoable end-to-end. Replace `MANIFEST_URL` / the mapping below when the
// real source is decided.
// ============================================================================

const MANIFEST_URL = "/wordClips/index.json";

// Returns { index, words } where
//   index = { word: [ { url, conf, quality }, ... ] }  (best conf first)
//   words = sorted usable word list
export async function loadClipIndex() {
  const res = await fetch(MANIFEST_URL);
  if (!res.ok) {
    throw new Error(
      `clip manifest not found at ${MANIFEST_URL} (${res.status}). ` +
      `Run cutWordClips.py to generate it (or a dev subset).`);
  }
  const raw = await res.json(); // { word: [ {file, conf, quality}, ... ] }
  const index = {};
  for (const [word, clips] of Object.entries(raw)) {
    index[word] = clips
      .map((c) => ({
        url: `/wordClips/${encodeURI(c.file)}`,
        conf: c.conf,
        quality: c.quality || "high",
      }))
      .sort((a, b) => b.conf - a.conf);
  }
  return { index, words: Object.keys(index).sort() };
}

// A word is low-quality iff it has no high-tier clip (mirrors
// wordCandidates.word_quality).
export function wordQuality(pool) {
  return pool.some((c) => c.quality === "high") ? "high" : "low";
}

// Confidence-weighted random pick (weight = conf**2), mirroring
// wordCandidates.weighted_pick.
export function weightedPick(pool) {
  const weights = pool.map((c) => c.conf * c.conf);
  const total = weights.reduce((a, b) => a + b, 0);
  if (total <= 0) return pool[Math.floor(Math.random() * pool.length)];
  let r = Math.random() * total;
  for (let i = 0; i < pool.length; i++) {
    r -= weights[i];
    if (r <= 0) return pool[i];
  }
  return pool[pool.length - 1];
}
