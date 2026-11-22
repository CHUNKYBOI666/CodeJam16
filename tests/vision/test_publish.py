import json
from urllib.error import URLError

import pytest

from vision.publish import Publisher, PublishError


def test_check_retries_with_original_event_and_current_secret_headers():
    calls = []
    event = {"episode_id": "episode-a"}

    def post(request, timeout):
        calls.append((request, timeout))
        if len(calls) == 1:
            raise URLError("offline")
        return {"id": "saved"}

    publisher = Publisher("https://example.test", "sb_secret_test", post=post, sleep=lambda _: None)
    assert publisher.publish_check(event) == {"id": "saved"}
    for request, timeout in calls:
        assert request.full_url.endswith("/rest/v1/rpc/create_check")
        assert request.get_header("Apikey") == "sb_secret_test"
        assert request.get_header("Authorization") is None
        assert json.loads(request.data) == {"p_event": event}
        assert timeout > 0


def test_publisher_reports_exhaustion_without_dumping_request():
    calls = []

    def post(request, timeout):
        calls.append(request)
        raise URLError("offline")

    publisher = Publisher("https://example.test", "secret", post=post, sleep=lambda _: None)
    with pytest.raises(PublishError, match="create_check") as caught:
        publisher.publish_check({"episode_id": "episode-a"})
    assert len(calls) == 5
    assert "secret" not in str(caught.value)


def test_heartbeat_is_throttled_but_transitions_send_immediately():
    now = [0]
    calls = []
    publisher = Publisher(
        "https://example.test",
        "test",
        post=lambda request, timeout: calls.append(json.loads(request.data)),
        clock=lambda: now[0],
    )
    publisher.report_feed("live", "Live", 12, "live")
    now[0] = 14
    publisher.report_feed("live", "Live", 13, "live")
    publisher.report_feed("offline", "Camera offline", 0, "live")
    now[0] = 29
    publisher.report_feed("offline", "Camera offline", 0, "live")
    assert [call["p_status"] for call in calls] == ["live", "offline", "offline"]
