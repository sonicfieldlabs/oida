import json
from pathlib import Path
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from akousma import AkousmataStore
from oida.research_bridge import research_router


def test_owner_delegates_to_one_service_and_replays_across_restart(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AKOUSMATA_PATH", str(tmp_path))
    monkeypatch.setenv("AKOUSMATA_WATCHER", "0")
    b = json.loads(
        (Path(__file__).parent / "fixtures/research-workflow.json").read_text()
    )
    store = AkousmataStore(tmp_path)
    for source in b["sources"]:
        store.put(source)

    def client():
        app = FastAPI()
        app.include_router(research_router())
        return TestClient(app)

    r = b["request"]
    c = client()
    result = c.post("/owner/research/proposals", json=r)
    assert result.status_code == 200, result.text
    assert client().post("/owner/research/proposals", json=r).json()["replayed"]
    rid = r["source_refs"][0]
    assert c.post(f"/owner/research/changes/{rid}").status_code == 200
    changes = c.get("/owner/research/reconcile").json()["changes"]
    assert any(x["record_id"] == rid for x in changes)
    item = next(x for x in changes if x["record_id"] == rid)
    assert (
        c.post(
            f"/owner/research/changes/{rid}/acknowledge", json={"sha256": "stale"}
        ).status_code
        == 409
    )
    assert (
        c.post(
            f"/owner/research/changes/{rid}/acknowledge",
            json={"sha256": item["sha256"]},
        ).status_code
        == 200
    )
    # Existing navigator watcher, already started by Oida lifecycle, owns scheduling.
    from akousmata_app import watcher

    with patch("akousmata_app.wiki.ingest"), patch("akousmata_app.wiki.diary_digest"):
        watcher.run_once()
    assert watcher.status()["last_research_reconcile_at"]
    assert store.get(r["record_id"])
    store.close()


from test_observation_source import setup  # noqa: F401, E402


def test_full_owner_mount_uses_existing_shared_service(setup, tmp_path):  # noqa: F811
    client = setup()
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/research-workflow.json").read_text()
    )
    store = AkousmataStore(tmp_path / "store")
    try:
        for source in fixture["sources"]:
            store.put(source)
        response = client.post("/owner/research/proposals", json=fixture["request"])
        assert response.status_code == 200, response.text
        assert store.get(fixture["request"]["record_id"])
        assert client.post("/owner/research/changes/missing").status_code == 400
        assert client.get("/owner/research/reconcile?limit=0").status_code == 400
    finally:
        store.close()
