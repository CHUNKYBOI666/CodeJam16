import tempfile
import unittest
from pathlib import Path

from codejam16.illness import IllnessEpisode
from codejam16.store import IncidentStore


def scored(end_ms, score):
    return {"status": "scored", "end_ms": end_ms, "nausea_vomiting_score": score,
            "competing_score": 1 - score}


class IllnessTests(unittest.TestCase):
    def test_model_scores_confirm_once_until_recovered(self):
        episode = IllnessEpisode()
        events = [episode.update(scored(4000 + i * 2000, p))
                  for i, p in enumerate([.81, .82, .94, .2, .1, .85, .89])]
        self.assertEqual([i for i, event in enumerate(events) if event], [1, 6])
        self.assertNotEqual(events[1]["episode_id"], events[6]["episode_id"])

    def test_weak_missing_and_skipped_windows_interrupt_confirmation(self):
        episode = IllnessEpisode()
        for window in [scored(4000, .6), scored(6000, .9),
                       {"status": "insufficient_evidence", "end_ms": 8000},
                       scored(10000, .9), scored(14000, .9)]:
            self.assertIsNone(episode.update(window))

    def test_illness_resolution_and_person_down_compatibility(self):
        with tempfile.TemporaryDirectory() as directory:
            store = IncidentStore(str(Path(directory) / "staff.sqlite"))
            actor = {"id": "ada", "name": "Ada", "role": "responder"}
            for signal, correct, wrong in [
                    ("possible_retching", "Assistance provided", "Confirmed person down"),
                    ("person_down", "Confirmed person down", "Assistance provided")]:
                event = {"episode_id": signal, "signal": signal, "source": "replay"}
                store.create_incident(event)
                store.create_incident(event)
                checks = store.snapshot(actor, {})["checks"]
                self.assertEqual(len(checks), 1)
                self.assertIn(correct, checks[0]["outcomes"])
                for action in ("accept", "arrive"):
                    self.assertTrue(store.transition(signal, actor, action)[0])
                self.assertFalse(store.transition(signal, actor, "resolve", wrong)[0])
                self.assertTrue(store.transition(signal, actor, "resolve", correct)[0])
