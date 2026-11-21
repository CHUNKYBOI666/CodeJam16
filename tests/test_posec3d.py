from contextlib import redirect_stderr
from io import StringIO
import json
import unittest
from unittest.mock import Mock

from codejam16.detector import PersonDownDetector
from codejam16.pose_trace import PoseTrace
from codejam16.posec3d_cli import sample_window, validate_row, windows
from test_detector import DOWN, FLOOR_BOX


def row(timestamp, *, shape=None, joints=True):
    return {
        "source_timestamp_ms": timestamp,
        "image_shape": shape or [500, 1000],
        "joints": [[100, 200, 0.9]] * 17 if joints else None,
    }


class PoseTraceTests(unittest.TestCase):
    def test_observer_reuses_inference_and_preserves_event(self):
        output = StringIO()
        predictor = Mock(side_effect=lambda frame: frame.results)
        detector = PersonDownDetector(
            predictor, floor_box=FLOOR_BOX, observe_pose=PoseTrace(output)
        )
        events = [detector.update(DOWN, time) for time in range(0, 3000, 500)]
        self.assertEqual(sum(event is not None for event in events), 1)
        self.assertEqual(predictor.call_count, 6)
        rows = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[-1]["source_timestamp_ms"], 2500)
        self.assertEqual(rows[0]["image_shape"], [500, 1000])
        self.assertEqual(len(rows[0]["joints"]), 17)

    def test_trace_failure_keeps_detector_producing_event(self):
        output = Mock()
        output.write.side_effect = OSError("disk full")
        trace = PoseTrace(output)
        detector = PersonDownDetector(
            lambda frame: frame.results, floor_box=FLOOR_BOX, observe_pose=trace
        )
        with redirect_stderr(StringIO()) as errors:
            events = [detector.update(DOWN, time) for time in range(0, 2500, 500)]
        self.assertEqual(sum(event is not None for event in events), 1)
        self.assertTrue(trace.failed)
        self.assertEqual(output.write.call_count, 1)
        self.assertIn("disk full", errors.getvalue())

    def test_missing_and_multiple_people_are_explicit(self):
        output = StringIO()
        trace = PoseTrace(output)
        trace(0, (500, 1000), [])
        trace(100, (500, 1000), DOWN.results * 2)
        rows = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([value["status"] for value in rows], ["missing", "ambiguous"])
        self.assertTrue(all(value["joints"] is None for value in rows))


class PoseWindowTests(unittest.TestCase):
    def test_incomplete_window_is_omitted(self):
        self.assertEqual(list(windows([row(0), row(500)], 1000, 500, 500)), [])

    def test_fixed_time_windows_preserve_missing_samples(self):
        observations = [row(t, joints=t != 500) for t in range(0, 2501, 100)]
        result = list(windows(observations, 1000, 500, 500))
        self.assertEqual([(a, b) for a, b, _ in result],
                         [(0, 1000), (500, 1500), (1000, 2000), (1500, 2500)])
        selected = sample_window(result[0][2], 0, 1000)
        self.assertEqual(len(selected), 48)
        self.assertIn(None, selected)

    def test_gap_and_dimension_change_restart_window(self):
        for changed in (row(1600), row(1000, shape=[600, 1000])):
            with self.subTest(changed=changed):
                first = [row(0), row(500), changed]
                start = changed["source_timestamp_ms"]
                rest = [row(start + offset, shape=changed["image_shape"])
                        for offset in (500, 1000)]
                result = list(windows(first + rest, 1000, 1000, 500))
                self.assertEqual([(a, b) for a, b, _ in result], [(start, start + 1000)])

    def test_invalid_and_backward_timestamps_fail(self):
        for timestamp in (-1, True, "500", float("nan"), float("inf")):
            with self.subTest(timestamp=timestamp), self.assertRaises(ValueError):
                list(windows([row(timestamp)], 1000, 500, 500))
        with self.assertRaises(ValueError):
            list(windows([row(100), row(100)], 1000, 500, 500))

    def test_invalid_joint_shape_or_value_fails(self):
        for joints in ([[1, 2, 0.9]], [[1, 2, 3]] * 17,
                       [[float("nan"), 2, 0.9]] * 17):
            value = row(0)
            value["joints"] = joints
            with self.assertRaises(ValueError):
                validate_row(value)

    def test_nonfinite_or_zero_window_options_fail(self):
        for duration in (0, -1, float("inf")):
            with self.assertRaises(ValueError):
                list(windows([], duration, 500, 500))
