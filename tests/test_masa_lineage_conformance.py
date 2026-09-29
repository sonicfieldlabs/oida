"""Canonical 0.2.1 fixture bytes tested through the installed owner boundary."""
import hashlib
import json
import os
from pathlib import Path

import pytest

from oida.observation_source import validator

FIXTURE = Path(__file__).parent / "fixtures/masa-lineage-cases.json"
CASES = json.loads(FIXTURE.read_text())


def test_canonical_fixture_identity():
    origin = json.loads(FIXTURE.with_suffix(".origin.json").read_text())
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == origin["sha256"]


@pytest.mark.parametrize("scenario", CASES, ids=[case["name"] for case in CASES])
def test_canonical_lineage_boundary(scenario, tmp_path):
    module = os.environ.get("OIDA_TEST_MASA_VALIDATOR_MODULE")
    if not module:
        pytest.skip("select the installed MASA validator for owner conformance")
    errors = validator(module)(scenario["record"])
    assert (not errors) == scenario["valid"], errors
