from pathlib import Path
from tempfile import TemporaryDirectory
import json
import re
import threading
import unittest
from uuid import uuid4

from codejam16.live import FeedState
from codejam16.web.app import create_app, lan_address


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "person-down.json"


def join(client, name="Ada"):
    response = client.post("/join", data={"name": name})
    state = client.get("/api/state").get_json() if response.status_code == 302 else {}
    return response, state.get("csrf_token", "")


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
        join(client)
        state = client.get("/api/state").get_json()
        self.assertEqual(state["feed"]["status"], "live")
        self.assertEqual(state["feed"]["fps"], 12.5)
        self.assertEqual({zone["id"] for zone in state["zones"]}, {"east-bar", "main-floor"})

    def test_default_feed_is_rehearsal_mode(self):
        client = self.make_app().test_client()
        join(client)
        self.assertEqual(client.get("/api/state").get_json()["feed"]["label"], "Rehearsal mode")

    def test_create_app_starts_no_worker(self):
        self.make_app()
        self.assertNotIn("detector-worker", [thread.name for thread in threading.enumerate()])

    def test_no_sample_check_by_default(self):
        client = self.make_app().test_client()
        join(client)
        self.assertEqual(client.get("/api/state").get_json()["checks"], [])

    def test_lan_address_is_an_ip(self):
        self.assertRegex(lan_address(), r"^\d+\.\d+\.\d+\.\d+$")


class LaunchTests(unittest.TestCase):
    def test_missing_weights_fail_without_download(self):
        from codejam16.web.app import predictor_loader
        with self.assertRaisesRegex(FileNotFoundError, "missing.pt"):
            predictor_loader(Path("/nonexistent/missing.pt"))()


class JoinTests(WebTestCase):
    def setUp(self):
        super().setUp()
        self.app = self.make_app()

    def test_join_page_shows_name_form_only(self):
        body = self.app.test_client().get("/join").get_data(as_text=True)
        self.assertIn('name="name"', body)
        self.assertNotIn("Supervisor", body)

    def test_old_token_link_shows_form(self):
        response = self.app.test_client().get("/join/responder-ada")
        self.assertEqual(response.status_code, 200)
        self.assertIn('name="name"', response.get_data(as_text=True))

    def test_join_sets_cookie_and_lands_on_checks(self):
        response = self.app.test_client().post("/join", data={"name": "Alex"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/"))
        cookie = response.headers["Set-Cookie"]
        self.assertIn("codejam_staff=", cookie)
        self.assertIn("HttpOnly", cookie)

    def test_blank_name_creates_nobody(self):
        client = self.app.test_client()
        response = client.post("/join", data={"name": "   "})
        self.assertEqual(response.status_code, 400)
        self.assertIn('name="name"', response.get_data(as_text=True))
        self.assertNotIn("Set-Cookie", response.headers)
        self.assertEqual(client.get("/api/state").status_code, 302)

    def test_cross_origin_join_403(self):
        client = self.app.test_client()
        response = client.post("/join", data={"name": "Alex"}, headers={"Origin": "http://evil.test"})
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("Set-Cookie", response.headers)

    def test_refresh_keeps_person(self):
        client = self.app.test_client()
        join(client, "Alex")
        first = client.get("/api/state").get_json()["viewer"]
        second = client.get("/api/state").get_json()["viewer"]
        self.assertEqual((first["name"], first["id"]), (second["name"], second["id"]))
        self.assertEqual(first["name"], "Alex")

    def test_other_phone_unchanged_after_rejoin(self):
        alex, jordan = self.app.test_client(), self.app.test_client()
        join(alex, "Alex")
        join(jordan, "Jordan")
        before = jordan.get("/api/state").get_json()["viewer"]
        alex.post("/join", data={"name": "Sam"})
        self.assertEqual(jordan.get("/api/state").get_json()["viewer"], before)
        self.assertEqual(alex.get("/api/state").get_json()["viewer"]["name"], "Sam")

    def test_stale_cookie_redirects_to_join(self):
        client = self.app.test_client()
        client.set_cookie("codejam_staff", "bogus")
        response = client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/join"))

    def test_checks_and_map_routes_serve_app(self):
        client = self.app.test_client()
        join(client)
        for path, tab in (("/", "checks"), ("/map", "map")):
            response = client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn(f'data-initial-tab="{tab}"', response.get_data(as_text=True))

    def test_change_name_page_prefills_current_name(self):
        client = self.app.test_client()
        join(client, "Alex")
        self.assertIn('value="Alex"', client.get("/join").get_data(as_text=True))


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

    def test_action_without_csrf_403(self):
        client = self.app.test_client()
        join(client)
        self.assertEqual(self.act(client, "", "accept").status_code, 403)

    def test_action_bad_origin_403(self):
        client = self.app.test_client()
        _, csrf = join(client)
        response = self.act(client, csrf, "accept", headers={"Origin": "http://evil.test"})
        self.assertEqual(response.status_code, 403)

    def test_two_clients_race_one_owner_and_loser_sees_winner(self):
        ada, jordan = self.app.test_client(), self.app.test_client()
        _, ada_csrf = join(ada, "Ada")
        _, jordan_csrf = join(jordan, "Jordan")
        barrier = threading.Barrier(2)
        responses = {}

        def claim(name, client, csrf):
            barrier.wait()
            responses[name] = self.act(client, csrf, "accept")

        threads = [threading.Thread(target=claim, args=args)
                   for args in (("Ada", ada, ada_csrf), ("Jordan", jordan, jordan_csrf))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(response.status_code for response in responses.values()), [200, 409])
        winner = next(name for name, response in responses.items() if response.status_code == 200)
        loser = "Jordan" if winner == "Ada" else "Ada"
        self.assertEqual(responses[loser].get_json()["error"], f"{winner} accepted this first.")

    def test_refresh_keeps_owner(self):
        client = self.app.test_client()
        _, csrf = join(client)
        self.act(client, csrf, "accept")
        state = client.get("/api/state").get_json()
        check = next(item for item in state["checks"] if item["id"] == self.check_id)
        self.assertEqual(check["owner_id"], state["viewer"]["id"])
        self.assertEqual(check["status"], "accepted")

    def test_rejoin_same_browser_renames_and_keeps_ownership(self):
        client = self.app.test_client()
        _, csrf = join(client, "Ada")
        self.act(client, csrf, "accept")
        client.post("/join", data={"name": "Sam"})
        csrf = client.get("/api/state").get_json()["csrf_token"]
        self.assertEqual(self.act(client, csrf, "arrive").status_code, 200)
        state = client.get("/api/state").get_json()
        check = next(item for item in state["checks"] if item["id"] == self.check_id)
        self.assertEqual((check["owner_name"], check["status"]), ("Sam", "arrived"))

    def test_resolved_moves_to_recent(self):
        client = self.app.test_client()
        _, csrf = join(client)
        for action in ("accept", "arrive"):
            self.assertEqual(self.act(client, csrf, action).status_code, 200)
        self.assertEqual(self.act(client, csrf, "resolve", "False alarm").status_code, 200)
        state = client.get("/api/state").get_json()
        self.assertNotIn(self.check_id, [item["id"] for item in state["checks"]])
        recent = next(item for item in state["recent"] if item["id"] == self.check_id)
        self.assertEqual(recent["outcome"], "False alarm")


if __name__ == "__main__":
    unittest.main()
