import math

import numpy as np
import pytest
import soundfile as sf

from oida.apertures import file_aperture
from oida.station_aperture import Aperture, PreviewRequest, preview


def test_preview_receipt_and_request_identity_bind_the_actual_window(tmp_path):
    path = tmp_path / "sample.wav"
    sf.write(path, np.zeros(48000), 48000)
    req = PreviewRequest(
        path=str(path),
        aperture=Aperture(mode="centaur"),
        start_seconds=0.25,
        seconds=0.5,
    )
    result = preview(req)
    assert result["aperture"]["request"]["window_s"] == [0.25, 0.75]
    whole = file_aperture(path)
    assert whole["source_sha256"] == result["aperture"]["source_sha256"]
    assert whole["request"]["request_id"] != result["aperture"]["request"]["request_id"]
    assert result["limits"][2]["status"] == "undetermined"


@pytest.mark.parametrize(
    "window", [[0, math.nan], [0.5, 0.2], [-1, 1], [0, 2], [True, 1]]
)
def test_window_outside_source_or_nonfinite_is_refused(tmp_path, window):
    path = tmp_path / "sample.wav"
    sf.write(path, np.zeros(8000), 8000)
    with pytest.raises(ValueError):
        file_aperture(path, window_s=window)
