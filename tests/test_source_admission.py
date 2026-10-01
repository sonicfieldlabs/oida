# ruff: noqa: F811
import hashlib
import json
from unittest.mock import patch

import numpy as np
import pytest
import soundfile as sf
from akousma import AkousmataStore, load_schema
from jsonschema import validate

from test_runtime_attribution import client  # noqa: F401


def request(tmp_path, adapter='file', source_type='file'):
    path = tmp_path / 'source.wav'
    sf.write(path, np.zeros(96000, dtype=np.float32), 96000)
    return dict(path=str(path), source_type=source_type, raw_audio_policy='external_ref',
                source_admission=dict(adapter=adapter, producer_id='fixture-producer',
                    source_id='fixture-source', source_time='2026-09-06T12:00:00Z',
                    consent='granted', consent_ref='fixture-permission',
                    raw_audio_policy='external_ref', max_window_s=1.0,
                    apparatus={'status': 'unknown'}))


@pytest.mark.parametrize(('adapter', 'source_type'), [('file', 'file'),
    ('browser-microphone', 'live_input'), ('high-rate-device', 'live_input'),
    ('radio-window', 'external_stream')])
def test_source_survives_owner_gateway_and_store(client, tmp_path, adapter, source_type):
    req = request(tmp_path, adapter, source_type)
    response = client.post('/gateway/listen', json={**req, 'remember': True})
    assert response.status_code == 200, response.text
    event = response.json()['listening_event']
    receipt = event['source']['details']['source_admission']
    assert receipt['subject_ref'] == 'sha256:' + hashlib.sha256((tmp_path/'source.wav').read_bytes()).hexdigest()
    assert receipt['sampled_representation'] == dict(sample_rate_hz=96000, channels=1, duration_s=1.0)
    assert receipt['apparatus'] == {'status': 'unknown'}
    assert event['segment']['captured_at'] == req['source_admission']['source_time']
    store = AkousmataStore(tmp_path/'store')
    try:
        record = store.get(store.query(limit=1)[0]['akousma_id'])
        validate(record, load_schema())
        assert json.dumps(receipt, sort_keys=True) in json.dumps(record, sort_keys=True)
    finally:
        store.close()


@pytest.mark.parametrize(('field', 'value', 'status'), [
    ('consent', 'denied', 400), ('consent', 'unknown', 400),
    ('raw_audio_policy', 'saved', 400), ('adapter', 'browser-microphone', 400),
    ('max_window_s', 0.5, 400), ('max_window_s', 301.0, 422),
    ('source_time', '2026-09-06T12:00:00', 422),
    ('apparatus', {'text': 'x'*16385}, 422),
    ('extra', True, 422),
])
def test_invalid_source_is_rejected_before_report(client, tmp_path, field, value, status):
    req = request(tmp_path)
    req['source_admission'][field] = value
    with patch('oida.server.report', side_effect=AssertionError('must not run')):
        response = client.post('/gateway/listen', json={**req, 'remember': True})
    assert response.status_code == status, response.text
    store = AkousmataStore(tmp_path/'store')
    try:
        assert not store.query(limit=1)
    finally:
        store.close()


def test_refused_source_can_retry_and_record_survives_reopen(client, tmp_path):
    req = request(tmp_path)
    req['source_admission']['consent'] = 'denied'
    assert client.post('/gateway/listen', json=req).status_code == 400
    req['source_admission']['consent'] = 'granted'
    result = client.post('/gateway/listen', json={**req, 'remember': True})
    assert result.status_code == 200, result.text
    store = AkousmataStore(tmp_path/'store')
    record_id = result.json()['akousma_id']
    before = store.get(record_id)
    store.close()
    reopened = AkousmataStore(tmp_path/'store')
    try:
        assert reopened.get(record_id) == before
    finally:
        reopened.close()
