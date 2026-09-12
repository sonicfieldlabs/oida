import copy
import json
from pathlib import Path
from oida.library_capture import retain_capture


def test_capture_copy_separate_from_memory_and_covenant_withholding(tmp_path):
    source = tmp_path / "input.wav"
    source.write_bytes(b"RIFFtest")
    record = {
        "akousma_id": "akm_test",
        "summary": "Rain",
        "auditum": {"listenings": []},
    }
    before = copy.deepcopy(record)
    root = tmp_path / "retained"
    kwargs = dict(
        record_id=record["akousma_id"],
        event_id="evt_test",
        label="Radio",
        duration_seconds=10.0,
        source_type="external_stream",
    )
    denied = retain_capture(source, root, blocked=True, **kwargs)
    assert denied["status"] == "withheld" and not root.exists()
    result = retain_capture(
        source, root, blocked=False, parent_sound_id="sound_parent", **kwargs
    )
    assert result["status"] == "retained"
    output = Path(result["path"])
    assert output.read_bytes() == source.read_bytes()
    sidecar = json.loads(output.with_suffix(".wav.json").read_text())
    assert sidecar["record_id"] == record["akousma_id"]
    assert sidecar["parent_sound_id"] == "sound_parent"
    assert len(sidecar["sha256"]) == 64
    assert record == before


def test_missing_capture_is_reported_without_creating_empty_library_entry(tmp_path):
    value = retain_capture(
        tmp_path / "gone.wav",
        tmp_path / "library",
        record_id=None,
        event_id="evt",
        label="Gone",
        duration_seconds=None,
        source_type="file",
        blocked=False,
    )
    assert value["status"] == "unavailable"
    assert not (tmp_path / "library").exists()
