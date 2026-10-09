import os
import subprocess
import sys


def test_workspace_identity_is_side_effect_free_and_mutations_are_bound(tmp_path):
    code = """
from fastapi.testclient import TestClient
from oida.server import create_app
app = create_app(profile='stub')
with TestClient(app, base_url='http://127.0.0.1:8765') as client:
    identity = client.get('/owner/identity').json()
    assert identity['owner'] == 'oida' and identity['mode'] == 'workspace'
    assert len(identity['binding']) == 64
    assert client.post('/background/pause').status_code == 409
    assert client.get('/owner/changes/capabilities').status_code == 409
    assert client.get('/owner/changes').status_code == 409
    headers = {'X-Centaur-Workspace': identity['workspace_id'], 'X-Centaur-Generation': identity['generation'], 'X-Centaur-Binding': identity['binding']}
    assert client.post('/background/pause', headers=headers).status_code == 200
    capabilities = client.get('/owner/changes/capabilities', headers=headers)
    assert capabilities.status_code == 200
    assert capabilities.json()['contract'] == 'oida/owner-change-stream/v1'
    assert capabilities.json()['payload'] == 'invalidation_only'
"""
    env = dict(os.environ)
    env.update(
        LISTENINGSTACK_WORKSPACE_ID="ws_0123456789abcdef01234567",
        LISTENINGSTACK_WORKSPACE_GENERATION="generation-one",
        OIDA_DATA_DIR=str(tmp_path / "data"),
        OIDA_AUDIO_DIR=str(tmp_path / "audio"),
        OIDA_TRIAL_DIR=str(tmp_path / "trial"),
        AKOUSMATA_PATH=str(tmp_path / "memory"),
    )
    result = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
