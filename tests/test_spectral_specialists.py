import os
import numpy as np
import pytest
from oida import spectral_specialists as workers


def test_missing_or_changed_deployment_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.delenv("OIDA_SPECTRAL_WORKERS_CONFIG", raising=False)
    assert workers.capabilities()["nsgt"]["status"] == "unavailable"
    path = tmp_path / "config.json"
    path.write_text("{}")
    monkeypatch.setenv("OIDA_SPECTRAL_WORKERS_CONFIG", str(path))
    with pytest.raises(ValueError, match="admission"):
        workers.run("nsgt", np.zeros((4096, 2)), 48000)


def test_restart_removes_only_abandoned_worker_data(tmp_path, monkeypatch):
    monkeypatch.setattr(workers.tempfile, 'gettempdir', lambda: str(tmp_path))
    abandoned = tmp_path/'oida-spectral-worker-99999999-fixture'
    active = tmp_path/f'oida-spectral-worker-{os.getpid()}-fixture'
    for path in (abandoned, active):
        path.mkdir()
        (path/'input.npy').write_bytes(b'fixture')
    workers.recover_temporary()
    assert not abandoned.exists() and active.exists()


@pytest.mark.parametrize("task", ["nsgt", "kymatio"])
def test_qualified_isolated_workers(task, monkeypatch):
    config = os.environ.get("OIDA_TEST_SPECTRAL_WORKERS_CONFIG")
    if not config:
        pytest.skip("Optional isolated environment not provisioned for this test run")
    monkeypatch.setenv("OIDA_SPECTRAL_WORKERS_CONFIG", config)
    from akousmata_app.derivatives import inspect_numeric

    data, meta = workers.run(task, np.zeros((4096, 2)), 48000)
    assert inspect_numeric(data)["shape"][0] == 2
    assert meta["losses"] and meta["settings"]["f_max"] == 24000
    with pytest.raises(ValueError, match="budget"):
        workers.run(task, np.zeros((16385, 2)), 48000)
