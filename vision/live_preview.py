"""Serve raw detector frames and the calibration editor on loopback."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from time import monotonic
from urllib.parse import urlsplit

import cv2


class CalibrationPreview:
    def __init__(self, port: int, source: int):
        editor = (Path(__file__).resolve().parent.parent / "tools/kitchen-calibration.html").read_bytes()
        self._frame = None
        self._next_frame_seconds = 0.0
        preview = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
                    self.send_error(400)
                    return
                path = urlsplit(self.path).path
                if path == "/":
                    self.respond(200, "text/html; charset=utf-8", editor)
                elif path == "/live.jpg":
                    frame = preview._frame
                    if frame is None or monotonic() - frame[0] > 3:
                        self.respond(503, "text/plain", b"Waiting for a detector frame.")
                    else:
                        self.respond(200, "image/jpeg", frame[1])
                else:
                    self.send_error(404)

            def respond(self, status, content_type, body):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Cross-Origin-Resource-Policy", "same-origin")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *_):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_port}/?live&source={source}"
        self._thread = Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
        self._thread.start()

    def update(self, image):
        now = monotonic()
        if now < self._next_frame_seconds:
            return
        self._next_frame_seconds = now + 0.25
        ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 84])
        if ok:
            self._frame = (now, jpeg.tobytes())

    def close(self):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
