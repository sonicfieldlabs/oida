from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from oida.apparatus_evidence import EvidenceUnavailable, resolve_evidence
from oida.apparatus_gate import gate_spectral_request
from oida.server import create_app
from test_runtime_attribution import client as client, fixture_request

NOW = datetime(2026, 9, 7, tzinfo=timezone.utc)


def registry(tmp_path, access):
    entry = {
        "ref": "evidence:source",
        "kind": "sampled_representation",
        "file": "evidence.json",
        "expires_at": "2099-01-01T00:00:00Z",
    }
    evidence = {
        "ref": entry["ref"],
        "kind": entry["kind"],
        "subject_ref": access["subject_ref"],
        "declaration": access["sampled_representation"],
    }
    data = json.dumps(evidence).encode()
    (tmp_path / "evidence.json").write_bytes(data)
    entry["sha256"] = hashlib.sha256(data).hexdigest()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"contract": "oida/apparatus-evidence/v1", "entries": [entry]})
    )
    return manifest


def test_exact_owner_declaration_resolves_and_tampering_does_not(tmp_path):
    request = fixture_request(tmp_path)
    access = request["listening_access"]
    manifest = registry(tmp_path, access)
    result = resolve_evidence(manifest, access, now=NOW)
    assert result["resolved_refs"] == ["evidence:source"]
    assert (
        result["manifest_sha256"] == hashlib.sha256(manifest.read_bytes()).hexdigest()
    )
    changed = json.loads(json.dumps(access))
    changed["subject_ref"] = "another-source"
    assert resolve_evidence(manifest, changed, now=NOW)["resolved_refs"] == []
    (tmp_path / "evidence.json").write_text("{}")
    with pytest.raises(EvidenceUnavailable, match="integrity"):
        resolve_evidence(manifest, access, now=NOW)


@pytest.mark.parametrize(
    "case", ["expired", "naive", "traversal", "symlink", "duplicate", "oversized"]
)
def test_bad_or_expired_registry_cannot_resolve(tmp_path, case):
    access = fixture_request(tmp_path)["listening_access"]
    manifest = registry(tmp_path, access)
    data = json.loads(manifest.read_text())
    entry = data["entries"][0]
    if case == "expired":
        entry["expires_at"] = "2000-01-01T00:00:00Z"
    if case == "naive":
        entry["expires_at"] = "2099-01-01"
    if case == "traversal":
        entry["file"] = "../outside.json"
    if case == "symlink":
        (tmp_path / "escape.json").symlink_to(tmp_path.parent / "outside.json")
        entry["file"] = "escape.json"
    if case == "duplicate":
        data["entries"].append(entry.copy())
    manifest.write_text(json.dumps(data))
    if case == "oversized":
        manifest.write_bytes(b" " * 131073)
    if case == "expired":
        result = resolve_evidence(manifest, access, now=NOW)
        assert (
            result["resolved_refs"] == []
            and result["unresolved"][0]["reason"] == "expired"
        )
    else:
        with pytest.raises(EvidenceUnavailable):
            resolve_evidence(manifest, access, now=NOW)


def test_source_reference_aliasing_never_grants_model_support(tmp_path):
    request = fixture_request(tmp_path)
    access = request["listening_access"]
    subject = access["subject_ref"]
    access["capture"] = {
        "status": "known",
        "apparatus_ref": subject,
        "supported_band_hz": {"lower": 0, "upper": 8000},
        "evidence_refs": [subject],
    }
    access["sampled_representation"]["representation_ref"] = subject
    access["sampled_representation"]["evidence_refs"] = [subject]
    access["model_input"] = {
        "status": "known",
        "model_ref": subject,
        "representation_ref": subject,
        "sample_rate_hz": 16000,
        "channels": 1,
        "effective_band_hz": {"lower": 0, "upper": 8000},
        "window_s": {"start": 0, "end": 1},
        "preprocessing_refs": [],
        "evidence_refs": [subject],
        "blind_spots": [],
    }
    request["spectral_request"]["band_hz"] = {"lower": 100, "upper": 1000}
    result = gate_spectral_request(
        Path(request["path"]), access, request["spectral_request"]
    )
    assert result["measurement_permitted"] is False
    assert result["support"] == "undetermined"
    assert "model_input support is unknown" in result["decision"]["reason"]


def test_owner_evidence_does_not_assert_model_input(tmp_path):
    request = fixture_request(tmp_path)
    manifest = registry(tmp_path, request["listening_access"])
    result = gate_spectral_request(
        Path(request["path"]),
        request["listening_access"],
        request["spectral_request"],
        evidence_manifest=manifest,
    )
    assert result["apparatus_evidence"]["resolved_refs"] == ["evidence:source"]
    assert result["measurement_permitted"] is False


def test_configured_registry_failure_is_503(client, tmp_path, monkeypatch):
    # Existing client fixture supplies isolated runtime/store configuration.
    monkeypatch.setenv("OIDA_APPARATUS_EVIDENCE", str(tmp_path / "missing.json"))
    configured = TestClient(create_app(profile="stub"), base_url="http://127.0.0.1")
    result = configured.post("/gateway/listen", json=fixture_request(tmp_path))
    assert result.status_code == 503, result.text


def test_boolean_cannot_equal_a_numeric_approval(tmp_path):
    access = fixture_request(tmp_path)["listening_access"]
    manifest = registry(tmp_path, access)
    path = tmp_path / "evidence.json"
    body = json.loads(path.read_text())
    body["declaration"]["retained_band_hz"]["lower"] = False
    path.write_text(json.dumps(body))
    index = json.loads(manifest.read_text())
    index["entries"][0]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(index))
    assert resolve_evidence(manifest, access, now=NOW)["resolved_refs"] == []
