"""
Plain static dev server for the Seinfeld mashup web frontend.

Serves the repo root so the page (/web/), the clip manifest
(/wordClips/index.json) and the clips (/wordClips/<word>/...) are all reachable
from one origin. Single-thread ffmpeg.wasm needs no special headers, so this is
deliberately minimal. Open the printed URL in a Chromium-based browser.
"""

import http.server
import socketserver
from functools import partial
from pathlib import Path

HOST, PORT = "127.0.0.1", 8000
ROOT = Path(__file__).resolve().parent.parent  # repo root


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".js": "text/javascript",
        ".mjs": "text/javascript",
        ".wasm": "application/wasm",
        ".json": "application/json",
        ".css": "text/css",
        ".mp4": "video/mp4",
    }

    def end_headers(self):
        # Harmless if same-origin; lets clips be fetched if ever cross-origin.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()


def main():
    handler = partial(Handler, directory=str(ROOT))
    with socketserver.ThreadingTCPServer((HOST, PORT), handler) as httpd:
        url = f"http://{HOST}:{PORT}/web/index.html"
        print(f"Serving {ROOT} at {url}")
        print("Ctrl+C to stop.")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped.")


if __name__ == "__main__":
    main()
