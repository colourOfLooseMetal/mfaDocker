// Clip index loader.
//
// Source: the line-clip inverted index (lineClips/index.json) produced by
// cutLineClips.py. Each n-gram text maps to a list of occurrences, ordered
// best-quality-first, of the form { file, start_in_clip, end_in_clip } -- a
// line clip plus the n-gram's start/end *inside* that clip. The app trims that
// sub-segment out of the line clip (see ffmpeg.js joinSegments) rather than
// joining whole clips.
//
// This mirrors buildNgramVideo.py's line-clip path. The slim manifest has no
// per-clip confidence, so selection is position-weighted (earlier = better).

// Where the .mp4 line clips live. Local for dev; in production swap for the
// DigitalOcean Space CDN, e.g.
//   "https://colm-extra-storage.nyc3.cdn.digitaloceanspaces.com/sfLines/"
const CLIPS_BASE = "https://colm-extra-storage.nyc3.cdn.digitaloceanspaces.com/sfLines/";
const MANIFEST_URL = "./lineClipsIndex.json";

// Returns { index, ngrams } where
//   index  = { ngram: [ { url, start, end }, ... ] }  (best-first, manifest order)
//   ngrams = sorted usable n-gram list (words and multi-word phrases)
export async function loadClipIndex() {
  const res = await fetch(MANIFEST_URL);
  if (!res.ok) {
    throw new Error(
      `clip manifest not found at ${MANIFEST_URL} (${res.status}). ` +
      `Run cutLineClips.py to generate it (or a dev subset).`);
  }
  const raw = await res.json(); // { ngram: [ {file, start_in_clip, end_in_clip}, ... ] }
  const index = {};
  for (const [ngram, occs] of Object.entries(raw)) {
    // Preserve manifest order -- it is already sorted highest-quality first.
    index[ngram] = occs.map((o) => ({
      url: `${CLIPS_BASE}${encodeURI(o.file)}`,
      start: o.start_in_clip,
      end: o.end_in_clip,
    }));
  }
  return { index, ngrams: Object.keys(index).sort() };
}

// Position-weighted random pick over a best-first pool (weight = 1/(i+1)**0.3),
// mirroring buildNgramVideo.build_video. Earlier (higher-quality) occurrences
// are favoured, but later ones remain reachable.
export function positionWeightedPick(pool) {
  const weights = pool.map((_, i) => 1 / (i + 1) ** 0.3);
  const total = weights.reduce((a, b) => a + b, 0);
  let r = Math.random() * total;
  for (let i = 0; i < pool.length; i++) {
    r -= weights[i];
    if (r <= 0) return pool[i];
  }
  return pool[pool.length - 1];
}
