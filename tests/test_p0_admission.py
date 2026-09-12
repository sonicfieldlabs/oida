import threading
import pytest
from akousma.resource_admission import admission_status
from oida.engine_mps import MpsMossEngine
from oida.operation_control import Control, controlled, OperationCancelled


def test_direct_prewarm_is_admitted_and_cancelled_before_load(tmp_path, monkeypatch):
    monkeypatch.setenv("LISTENINGSTACK_RESOURCE_DIR", str(tmp_path))
    engine = object.__new__(MpsMossEngine)
    engine._lock = threading.Lock()
    engine.model_id_for_kind = lambda kind: "existing-settings"
    seen = []
    engine._load_pair = lambda identifier: seen.append(
        (identifier, admission_status()["busy"])
    )
    engine.prewarm()
    assert seen == [("existing-settings", True)]
    assert not admission_status()["busy"]
    control = Control()
    with controlled(control):
        control.cancel()
        with pytest.raises(OperationCancelled):
            engine.prewarm()
    assert len(seen) == 1
