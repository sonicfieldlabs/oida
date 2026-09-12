import numpy as np
import soundfile as sf
from oida.apertures import file_aperture


def test_digital_and_physical_gates_are_distinct(tmp_path):
    path = tmp_path / "fixture.wav"
    sf.write(path, np.zeros(48000), 48000)
    assert (
        file_aperture(path, bands_hz=[[39000, 41000]])["decision"]["outcome"]
        == "refused"
    )
    assert (
        file_aperture(path, bands_hz=[[900, 1100]])["decision"]["outcome"]
        == "permitted"
    )
    physical = file_aperture(
        path, bands_hz=[[900, 1100]], claim_kind="physical_capture"
    )
    assert physical["decision"]["bands"][0]["support"] == "undetermined"
    assert (
        file_aperture(path, claim_kind="model_input")["decision"]["outcome"]
        == "refused"
    )
