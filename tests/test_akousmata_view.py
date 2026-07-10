"""Embedded akousmata history view tests (temp shared store)."""
from __future__ import annotations

import os
import tempfile
import unittest

import akousma
from fastapi.testclient import TestClient

from oida.server import create_app


class AkousmataViewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AKOUSMATA_PATH"] = self.tmp.name
        store = akousma.AkousmataStore(self.tmp.name)
        parent = akousma.new_akousma(
            audio={"asset_id": "cap_1"},
            originating_app="oida",
            origin="live-input",
            summary="harbor at dusk",
            tags=["harbor"],
        )
        store.put(parent)
        child = akousma.new_akousma(
            audio={"asset_id": "gen_1"},
            originating_app="germ",
            origin="generated",
            source_type="generated",
            parent_akousma_ids=[parent["akousma_id"]],
            relations=[akousma.relation("variant_of", parent["akousma_id"])],
            prompt="metallic harbor",
        )
        store.put(child)
        store.close()
        self.parent_id = parent["akousma_id"]
        self.child_id = child["akousma_id"]
        self.client = TestClient(create_app(profile="stub"), base_url="http://127.0.0.1")

    def tearDown(self) -> None:
        os.environ.pop("AKOUSMATA_PATH", None)
        self.tmp.cleanup()

    def test_list_and_filters(self) -> None:
        data = self.client.get("/akousmata/records").json()
        self.assertEqual(len(data["records"]), 2)
        oida_only = self.client.get("/akousmata/records", params={"app": "oida"}).json()["records"]
        self.assertEqual([r["akousma_id"] for r in oida_only], [self.parent_id])
        text = self.client.get("/akousmata/records", params={"text": "metallic"}).json()["records"]
        self.assertEqual([r["akousma_id"] for r in text], [self.child_id])

    def test_detail_lineage_and_kinship(self) -> None:
        detail = self.client.get(f"/akousmata/records/{self.child_id}").json()
        self.assertEqual(detail["parents"][0]["akousma_id"], self.parent_id)
        self.assertEqual(detail["related"][0]["type"], "variant_of")
        self.assertFalse(detail["audio_available"])
        self.assertEqual(self.client.get("/akousmata/records/akm_missing").status_code, 404)

    def test_tags(self) -> None:
        tags = {t["tag"]: t["count"] for t in self.client.get("/akousmata/tags").json()["tags"]}
        self.assertEqual(tags.get("harbor"), 1)


if __name__ == "__main__":
    unittest.main()
