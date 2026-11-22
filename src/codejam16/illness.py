"""Convert consecutive NTU60 model decisions into a single welfare-check episode."""

from uuid import uuid4


class IllnessEpisode:
    def __init__(self):
        self.active = False
        self.positive_windows = 0
        self.clear_windows = 0
        self.previous_end_ms = None

    def update(self, window):
        end_ms = window["end_ms"]
        if self.previous_end_ms is None or abs(end_ms - self.previous_end_ms - 2000) > 1:
            self.positive_windows = self.clear_windows = 0
        self.previous_end_ms = end_ms
        if window["status"] != "scored":
            self.positive_windows = self.clear_windows = 0
            return None

        score = window["nausea_vomiting_score"]
        positive = score >= 0.70 and score > window["competing_score"]
        self.positive_windows = self.positive_windows + 1 if positive else 0
        self.clear_windows = self.clear_windows + 1 if score <= 0.30 else 0
        if self.clear_windows >= 2:
            self.active = False
        if self.active or self.positive_windows < 2:
            return None
        self.active = True
        return {"episode_id": str(uuid4()), "signal": "possible_retching",
                "source_timestamp_ms": end_ms, "model_score": score}
