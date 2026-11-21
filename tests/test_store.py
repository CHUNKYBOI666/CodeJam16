from datetime import timedelta
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

from codejam16.store import IncidentStore, iso, utc_now


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "person-down.json"
ADA = {"id": "responder-ada", "name": "Ada Chen", "role": "responder"}
JORDAN = {"id": "responder-jordan", "name": "Jordan Lee", "role": "responder"}
OUTCOMES = ("Confirmed person down", "False alarm", "Couldn't verify")


def event(**overrides):
    value = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    value["episode_id"] = str(uuid4())
    value["detected_at"] = iso(utc_now())
    value.update(overrides)
    return value


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = IncidentStore(str(Path(directory.name) / "staff.sqlite3"))

    def new_check(self, **overrides):
        return self.store.create_incident(event(**overrides))["id"]

    def history(self, incident_id):
        with self.store.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM history WHERE incident_id=? ORDER BY id", (incident_id,))]


class ClaimTests(StoreTestCase):
    def test_concurrent_accept_has_one_winner(self):
        for _ in range(50):
            incident_id = self.new_check()
            barrier = threading.Barrier(2)
            results = {}

            def claim(actor):
                barrier.wait()
                results[actor["id"]] = self.store.transition(incident_id, actor, "accept")

            threads = [threading.Thread(target=claim, args=(actor,)) for actor in (ADA, JORDAN)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            winners = [actor_id for actor_id, (ok, _, _) in results.items() if ok]
            self.assertEqual(len(winners), 1)
            loser_id = ({ADA["id"], JORDAN["id"]} - set(winners)).pop()
            ok, message, incident = results[loser_id]
            winner_name = {ADA["id"]: ADA["name"], JORDAN["id"]: JORDAN["name"]}[winners[0]]
            self.assertEqual(message, f"{winner_name} accepted this first.")
            self.assertEqual(incident["owner_id"], winners[0])

    def test_non_owner_cannot_arrive_or_resolve(self):
        incident_id = self.new_check()
        self.store.transition(incident_id, ADA, "accept")
        ok, _, _ = self.store.transition(incident_id, JORDAN, "arrive")
        self.assertFalse(ok)
        self.assertEqual(self.store.get_incident(incident_id)["status"], "accepted")
        self.store.transition(incident_id, ADA, "arrive")
        ok, _, _ = self.store.transition(incident_id, JORDAN, "resolve", "False alarm")
        self.assertFalse(ok)
        self.assertEqual(self.store.get_incident(incident_id)["status"], "arrived")

    def test_resolve_requires_arrived_and_valid_outcome(self):
        incident_id = self.new_check()
        self.store.transition(incident_id, ADA, "accept")
        ok, _, _ = self.store.transition(incident_id, ADA, "resolve", "False alarm")
        self.assertFalse(ok)
        self.store.transition(incident_id, ADA, "arrive")
        ok, message, _ = self.store.transition(incident_id, ADA, "resolve", "Fine")
        self.assertFalse(ok)
        self.assertEqual(message, "Choose a resolution outcome")
        self.assertEqual(self.store.get_incident(incident_id)["status"], "arrived")

    def test_each_outcome_saved(self):
        for outcome in OUTCOMES:
            incident_id = self.new_check()
            for action in ("accept", "arrive"):
                self.store.transition(incident_id, ADA, action)
            ok, _, incident = self.store.transition(incident_id, ADA, "resolve", outcome)
            self.assertTrue(ok)
            self.assertEqual(incident["outcome"], outcome)
            self.assertTrue(incident["resolved_at"].endswith("Z"))
            last = self.history(incident_id)[-1]
            self.assertEqual((last["event"], last["actor_id"], last["note"]),
                             ("resolved", "responder-ada", outcome))

    def test_anyone_can_accept(self):
        incident_id = self.new_check()
        ok, _, incident = self.store.transition(
            incident_id, {"id": "p-1", "name": "Alex", "role": "supervisor"}, "accept")
        self.assertTrue(ok)
        self.assertEqual(incident["owner_name"], "Alex")


class CreateTests(StoreTestCase):
    def test_duplicate_episode_creates_one_check(self):
        value = event()
        self.store.create_incident(value)
        self.store.create_incident(dict(value))
        self.assertEqual(self.store.count(), 1)
        self.assertEqual([row["event"] for row in self.history(value["episode_id"])], ["created"])

    def test_history_failure_rolls_back(self):
        incident_id = self.new_check()
        with patch.object(self.store, "_history", side_effect=sqlite3.OperationalError("disk full")):
            with self.assertRaises(sqlite3.OperationalError):
                self.store.transition(incident_id, ADA, "accept")
        incident = self.store.get_incident(incident_id)
        self.assertEqual(incident["status"], "needs_responder")
        self.assertIsNone(incident["owner_id"])

    def test_overdue_flag(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        store = IncidentStore(str(Path(directory.name) / "overdue.sqlite3"), overdue_seconds=1)
        incident_id = store.create_incident(
            event(detected_at=iso(utc_now() - timedelta(seconds=5))))["id"]
        checks = store.snapshot(ADA, {})["checks"]
        self.assertTrue(checks[0]["overdue"])
        store.transition(incident_id, ADA, "accept")
        self.assertFalse(store.snapshot(ADA, {})["checks"][0]["overdue"])



class SeedTests(StoreTestCase):
    def test_seed_demo_only_main_floor_sample(self):
        self.store.seed_demo()
        checks = self.store.snapshot(ADA, {})["checks"]
        self.assertEqual([(check["zone_id"], check["source"]) for check in checks],
                         [("main-floor", "rehearsal")])
        self.assertTrue(checks[0]["overdue"])


class JoinTests(StoreTestCase):
    def accept_as(self, person, incident_id):
        return self.store.transition(incident_id, person, "accept")

    def test_blank_name_refused(self):
        self.assertIsNone(self.store.join_by_name("   "))

    def test_name_trimmed_and_capped(self):
        _, _, person = self.store.join_by_name("  " + "x" * 60 + " ")
        self.assertEqual(person["name"], "x" * 40)
        self.assertEqual(person["role"], "responder")

    def test_two_people_same_name_are_distinct(self):
        _, _, first = self.store.join_by_name("Alex")
        _, _, second = self.store.join_by_name("Alex")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual((first["name"], second["name"]), ("Alex", "Alex"))

    def test_rejoin_keeps_person_and_ownership(self):
        token, _, alex = self.store.join_by_name("Alex")
        incident_id = self.new_check()
        self.accept_as(alex, incident_id)
        again, _, sam = self.store.join_by_name("Sam", existing_token=token)
        self.assertEqual((again, sam["id"], sam["name"]), (token, alex["id"], "Sam"))
        self.assertEqual(self.store.get_session(token)["name"], "Sam")
        self.assertTrue(self.store.transition(incident_id, sam, "arrive")[0])
        self.assertTrue(self.store.transition(incident_id, sam, "resolve", "False alarm")[0])

    def test_rename_updates_open_checks_only(self):
        token, _, alex = self.store.join_by_name("Alex")
        closed, open_ = self.new_check(), self.new_check()
        for action, outcome in (("accept", None), ("arrive", None), ("resolve", "False alarm")):
            self.store.transition(closed, alex, action, outcome)
        self.accept_as(alex, open_)
        self.store.join_by_name("Sam", existing_token=token)
        self.assertEqual(self.store.get_incident(open_)["owner_name"], "Sam")
        self.assertEqual(self.store.get_incident(closed)["owner_name"], "Alex")

    def test_invalid_existing_token_creates_new_person(self):
        token, _, person = self.store.join_by_name("Alex", existing_token="bogus")
        self.assertNotEqual(token, "bogus")
        self.assertEqual(self.store.get_session(token)["id"], person["id"])


if __name__ == "__main__":
    unittest.main()
