from pathlib import Path
from tempfile import TemporaryDirectory
import json
import threading
import unittest
from uuid import uuid4

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



FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "person-down.json"


class WorkflowTests(WebTestCase):
    def setUp(self):
        super().setUp()
        self.app = self.make_app()
        event = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        event["episode_id"] = str(uuid4())
        self.check_id = self.app.extensions["incident_store"].create_incident(event)["id"]

    def act(self, client, csrf, action, outcome=None, headers=None):
        return client.post(f"/api/checks/{self.check_id}/actions",
                           json={"action": action, "outcome": outcome},
                           headers={"X-CSRF-Token": csrf, **(headers or {})})

    def test_valid_join_sets_cookie_and_redirects(self):
        response = self.app.test_client().post("/join/responder-ada")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/"))
        cookie = response.headers["Set-Cookie"]
        self.assertIn("codejam_staff=", cookie)
        self.assertIn("HttpOnly", cookie)

    def test_invalid_join_404(self):
        self.assertEqual(self.app.test_client().get("/join/nope").status_code, 404)

    def test_action_without_csrf_403(self):
        client = self.app.test_client()
        join(client, "responder-ada")
        self.assertEqual(self.act(client, "", "accept").status_code, 403)

    def test_action_bad_origin_403(self):
        client = self.app.test_client()
        _, csrf = join(client, "responder-ada")
        response = self.act(client, csrf, "accept", headers={"Origin": "http://evil.test"})
        self.assertEqual(response.status_code, 403)

    def test_supervisor_action_409(self):
        client = self.app.test_client()
        _, csrf = join(client, "supervisor-morgan")
        self.assertEqual(self.act(client, csrf, "accept").status_code, 409)

    def test_two_clients_race_one_owner(self):
        ada, jordan = self.app.test_client(), self.app.test_client()
        _, ada_csrf = join(ada, "responder-ada")
        _, jordan_csrf = join(jordan, "responder-jordan")
        barrier = threading.Barrier(2)
        responses = {}

        def claim(name, client, csrf):
            barrier.wait()
            responses[name] = self.act(client, csrf, "accept")

        threads = [threading.Thread(target=claim, args=args)
                   for args in (("ada", ada, ada_csrf), ("jordan", jordan, jordan_csrf))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        codes = sorted(response.status_code for response in responses.values())
        self.assertEqual(codes, [200, 409])
        winner = next(name for name, response in responses.items() if response.status_code == 200)
        loser = responses["jordan" if winner == "ada" else "ada"].get_json()
        self.assertEqual(loser["check"]["owner_name"], {"ada": "Ada Chen", "jordan": "Jordan Lee"}[winner])

    def test_refresh_keeps_owner(self):
        client = self.app.test_client()
        _, csrf = join(client, "responder-ada")
        self.act(client, csrf, "accept")
        state = client.get("/api/state").get_json()
        check = next(item for item in state["checks"] if item["id"] == self.check_id)
        self.assertEqual(check["owner_id"], state["viewer"]["id"])
        self.assertEqual(check["status"], "accepted")

    def test_resolved_moves_to_recent(self):
        client = self.app.test_client()
        _, csrf = join(client, "responder-ada")
        for action in ("accept", "arrive"):
            self.assertEqual(self.act(client, csrf, action).status_code, 200)
        self.assertEqual(self.act(client, csrf, "resolve", "False alarm").status_code, 200)
        state = client.get("/api/state").get_json()
        self.assertNotIn(self.check_id, [item["id"] for item in state["checks"]])
        recent = next(item for item in state["recent"] if item["id"] == self.check_id)
        self.assertEqual(recent["outcome"], "False alarm")


if __name__ == "__main__":
    unittest.main()
