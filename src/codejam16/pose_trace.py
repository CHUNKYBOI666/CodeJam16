"""Save local COCO poses for offline action recognition."""

import json
from math import isfinite
import sys


def _trace_pose(results, height, width):
    people = [
        (result.keypoints, index)
        for result in results if result.keypoints is not None
        for index in range(len(result.keypoints.xy))
    ]
    if len(people) != 1:
        return None, "ambiguous" if people else "missing"
    keypoints, index = people[0]
    if keypoints.conf is None:
        return None, "missing"
    coordinates, scores = keypoints.xy[index], keypoints.conf[index]
    if len(coordinates) != 17 or len(scores) != 17:
        return None, "invalid"
    joints = []
    for (x, y), score in zip(coordinates, scores):
        x, y, score = float(x), float(y), float(score)
        if 0 <= x < width and 0 <= y < height and isfinite(score) and 0 < score <= 1:
            joints.append([x, y, score])
        else:
            joints.append([0, 0, 0])
    return joints, "observed"


class PoseTrace:
    """Disable the optional sink on write failure while replay continues."""

    def __init__(self, output):
        self.output = output
        self.failed = False

    def __call__(self, timestamp_ms, image_shape, results):
        if self.failed:
            return
        height, width = image_shape
        joints, status = _trace_pose(results, height, width)
        try:
            self.output.write(json.dumps({
                "source_timestamp_ms": timestamp_ms,
                "image_shape": [height, width],
                "joints": joints,
                "status": status,
            }, allow_nan=False) + "\n")
            self.output.flush()
        except OSError as error:
            self.failed = True
            print(f"Pose trace stopped: {error}", file=sys.stderr)
