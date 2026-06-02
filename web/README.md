# Seinfeld Word Mashup — web frontend

A browser version of `buildWordVideo.py`: pick words, and the clips are joined
into a downloadable MP4 **entirely client-side** with `ffmpeg.wasm`. No video
backend — just a static file server.

## Run

1. Generate clips + manifest (or a dev subset) with `cutWordClips.py` so
   `wordClips/index.json` and the `.mp4`s exist.
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
- `js/ffmpeg.js` — engine singleton + `joinClips({mode, gap})`
- `js/clipIndex.js` — **INTERIM** clip-index loader (see TODO inside)
- `js/app.js` — UI
- `serve.py` — plain static dev server (repo root)

## Known TODO / future work

- **Word/phrase list source is not finalized.** `js/clipIndex.js` currently
  reads `wordClips/index.json` as a placeholder; the curated web list (built
  once, possibly with multi-word phrases) will replace it.
- **Server-side join fallback** (`/api/join`) is stubbed only.
- Combining uses `-c copy`, which requires uniform clips — `cutWordClips.py`
  normalizes every clip to 720×540 (`CLIP_W/CLIP_H` in `buildWordVideo.py`).
