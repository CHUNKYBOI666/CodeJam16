from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest

from codejam16.live import FeedState
from codejam16.web.app import create_app


def join(client, token):
    response = client.post(f"/join/{token}")
    state = client.get("/api/state").get_json()
    return response, state["csrf_token"]


class WebTestCase(unittest.TestCase):
    def setUp(self):
        self._directory = TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.db_path = str(Path(self._directory.name) / "staff.sqlite3")

    def make_app(self, **kwargs):
        kwargs.setdefault("seed_demo", False)
        app = create_app(self.db_path, **kwargs)
        app.testing = True
        return app


class AppWiringTests(WebTestCase):
    def test_state_includes_feed_and_zones(self):
        feed = FeedState()
        feed.update(status="live", label="Live camera", fps=12.5)
        client = self.make_app(feed=feed).test_client()
        join(client, "responder-ada")
        state = client.get("/api/state").get_json()
        self.assertEqual(state["feed"]["status"], "live")
        self.assertEqual(state["feed"]["fps"], 12.5)
        self.assertEqual({zone["id"] for zone in state["zones"]}, {"east-bar", "main-floor"})

    def test_default_feed_is_rehearsal_mode(self):
        client = self.make_app().test_client()
        join(client, "responder-ada")
        self.assertEqual(client.get("/api/state").get_json()["feed"]["label"], "Rehearsal mode")

    def test_create_app_starts_no_worker(self):
        self.make_app()
        self.assertNotIn("detector-worker", [thread.name for thread in threading.enumerate()])



class LaunchTests(unittest.TestCase):
    def test_missing_weights_fail_without_download(self):
        from codejam16.web.app import predictor_loader
        with self.assertRaisesRegex(FileNotFoundError, "missing.pt"):
            predictor_loader(Path("/nonexistent/missing.pt"))()


if __name__ == "__main__":
    unittest.main()
