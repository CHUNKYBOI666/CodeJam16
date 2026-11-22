from urllib.error import HTTPError
from urllib.request import urlopen

import cv2
import numpy as np
import pytest

from live_preview import CalibrationPreview


def test_editor_serves_raw_frames_from_the_detector_on_loopback():
    with CalibrationPreview(0, 1) as preview:
        base = preview.url.split("?", 1)[0]
        with pytest.raises(HTTPError) as error:
            urlopen(base + "live.jpg", timeout=2)
        assert error.value.code == 503
        image = np.full((108, 192, 3), 95, dtype=np.uint8)
        preview.update(image)
        with urlopen(base + "live.jpg", timeout=2) as response:
            assert response.headers["Content-Type"] == "image/jpeg"
            assert response.headers["Cache-Control"] == "no-store"
            frame = cv2.imdecode(np.frombuffer(response.read(), np.uint8), cv2.IMREAD_COLOR)
        assert frame.shape == image.shape
        assert np.max(np.abs(frame.astype(int) - image)) <= 2
        with urlopen(preview.url, timeout=2) as response:
            assert b"Freeze frame" in response.read()
