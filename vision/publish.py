"""Send welfare events and feed metadata to the hosted database."""

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class PublishError(RuntimeError):
    """The hosted database did not acknowledge a publish request."""


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _post(request, timeout):
    opener = build_opener(_RejectRedirects())
    with opener.open(request, timeout=timeout) as response:
        return json.loads(response.read() or b"null")


class Publisher:
    def __init__(self, url, service_key, post=_post, sleep=time.sleep, clock=time.monotonic):
        self.url = url.rstrip("/")
        self.headers = {"apikey": service_key, "Content-Type": "application/json"}
        if service_key.startswith("eyJ"):
            self.headers["Authorization"] = f"Bearer {service_key}"
        self.post = post
        self.sleep = sleep
        self.clock = clock
        self.last_feed = None
        self.last_feed_at = float("-inf")

    @classmethod
    def from_env(cls):
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        if not url or not key:
            raise PublishError("Set SUPABASE_URL and SUPABASE_SECRET_KEY before publishing.")
        return cls(url, key)

    def _rpc(self, function, values, attempts=1):
        request = Request(
            f"{self.url}/rest/v1/rpc/{function}",
            data=json.dumps(values, allow_nan=False).encode(),
            headers=self.headers,
            method="POST",
        )
        for attempt in range(attempts):
            try:
                return self.post(request, timeout=3)
            except HTTPError as error:
                status = error.code
                error.close()
                if status < 500 and status not in (408, 429):
                    raise PublishError(f"{function} refused the request (HTTP {status}).") from None
            except (URLError, OSError, ValueError):
                pass
            if attempt + 1 < attempts:
                self.sleep(0.25 * 2 ** attempt)
        raise PublishError(f"{function} failed after {attempts} attempt(s).")

    def publish_check(self, event: dict) -> dict:
        result = self._rpc("create_check", {"p_event": event}, attempts=5)
        if isinstance(result, list) and len(result) == 1:
            result = result[0]
        if not isinstance(result, dict) or not result.get("id"):
            raise PublishError("create_check did not return a saved check.")
        return result

    def report_feed(self, status: str, label: str, fps: float, source: str) -> None:
        now = self.clock()
        feed = (status, label, source)
        if feed == self.last_feed and now - self.last_feed_at < 15:
            return
        self._rpc("report_feed", {
            "p_status": status, "p_label": label, "p_fps": round(fps, 1), "p_source": source,
        })
        self.last_feed = feed
        self.last_feed_at = now


class BackgroundPublisher:
    """Bounded, ordered delivery off the frame loop."""

    def __init__(self, publisher):
        self.publisher = publisher
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="welfare-publish")
        self.checks = []
        self.feed = None
        self.feed_args = None
        self.next_feed_at = float("-inf")
        self.failed_checks = False

    def _submit(self, operation, *args, check=False):
        def send():
            try:
                operation(*args)
            except PublishError as error:
                if check:
                    self.failed_checks = True
                print(f"[publish] {error}", file=sys.stderr)
        return self.executor.submit(send)

    def check(self, event):
        self.checks = [job for job in self.checks if not job.done()]
        if len(self.checks) >= 8:
            self.failed_checks = True
            print("[publish] Check queue full. See the local alert JSONL for the event.", file=sys.stderr)
            return
        self.checks.append(self._submit(self.publisher.publish_check, event, check=True))

    def report(self, status, label, fps, source):
        args = (status, label, source)
        now = time.monotonic()
        if self.feed is not None and not self.feed.done():
            return
        if args != self.feed_args or now >= self.next_feed_at:
            self.feed_args = args
            self.next_feed_at = now + 15
            self.feed = self._submit(self.publisher.report_feed, status, label, fps, source)

    def close(self, status, label, source):
        self._submit(self.publisher.report_feed, status, label, 0.0, source)
        self.executor.shutdown(wait=True)
