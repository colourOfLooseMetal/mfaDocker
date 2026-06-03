# Seinfeld Word Mashup — web frontend

A browser version of `buildNgramVideo.py`: pick words or phrases, and the
matching n-gram sub-segments are trimmed out of their line clips and joined into
a downloadable MP4 **entirely client-side** with `ffmpeg.wasm`. No video
backend — just a static file server.

## Run

1. Generate line clips + manifest (or a dev subset) with `cutLineClips.py` so
   `lineClips/index.json` and the `sfvL_<N>.mp4`s exist.
2. From the repo root: `python web/serve.py`
3. Open the printed URL (Chromium-based browser recommended).

The ffmpeg engine is **vendored** under `web/ffmpeg/` (loaded same-origin, no
CDN / offline-friendly). The 31 MB `ffmpeg-core.wasm` is gitignored; re-fetch
the engine files with:

```
cd web/ffmpeg
curl -sLO https://cdn.jsdelivr.net/npm/@ffmpeg/util@0.12.1/dist/umd/index.js   # -> rename to util.js
curl -sLO https://cdn.jsdelivr.net/npm/@ffmpeg/ffmpeg@0.12.15/dist/umd/ffmpeg.js
curl -sLO https://cdn.jsdelivr.net/npm/@ffmpeg/ffmpeg@0.12.15/dist/umd/814.ffmpeg.js
curl -sLO https://cdn.jsdelivr.net/npm/@ffmpeg/core@0.12.10/dist/umd/ffmpeg-core.js
curl -sLO https://cdn.jsdelivr.net/npm/@ffmpeg/core@0.12.10/dist/umd/ffmpeg-core.wasm
```

Engine wiring gotcha (already handled in `js/ffmpeg.js`): do **not** pass
`classWorkerURL` to `ff.load()` — in 0.12.15 that spawns a module worker
against a hardcoded `file://` base and fails. Omitting it auto-locates the
correct same-origin classic worker (`814.ffmpeg.js`) next to the main script.

## Themes

`?theme=kitsch` (default, full 90s GeoCities) or `?theme=clean`.

## Files

- `index.html` — page shell, loads a theme + the UMD ffmpeg globals + `js/app.js`
- `themes/kitsch.css`, `themes/clean.css`
- `js/ffmpeg.js` — engine singleton + `joinSegments(segments, {gap})`: trims
  each n-gram sub-segment out of its line clip, then concat-copies
- `js/clipIndex.js` — line-clip index loader + `positionWeightedPick`
- `js/app.js` — UI
- `serve.py` — plain static dev server (repo root)

## Clip source

`js/clipIndex.js` reads the line-clip inverted index `lineClips/index.json`
(n-gram text → `{file, start_in_clip, end_in_clip}`, best-quality-first). The
`.mp4`s are fetched from `CLIPS_BASE` in `clipIndex.js`, which defaults to the
local `/lineClips/` for dev. In production, swap it for the DigitalOcean Space
CDN, e.g. `https://colm-extra-storage.nyc3.cdn.digitaloceanspaces.com/sfLines/`.

The slim manifest has no per-clip confidence, so selection is position-weighted
(`weight = 1/(i+1)**0.3`), favouring earlier (higher-quality) occurrences while
keeping later ones reachable — mirroring `buildNgramVideo.build_video`.

## Known TODO / future work

- **Server-side join fallback** (`/api/join`) is stubbed only.
- Combining uses `-c copy`, which requires uniform clips. Line clips are all
  normalized to 640×360 / 30 fps / 48 kHz by `cutLineClips.py`, and
  `joinSegments` re-encodes each trimmed sub-segment to those same locked params
  (`CRF`/`FPS`/`AR` in `js/ffmpeg.js`) so the concat-copy is safe.
