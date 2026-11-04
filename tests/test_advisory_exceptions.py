"""An overdue exception must fail before it can suppress an audit finding."""
from copy import deepcopy
from datetime import date
import json
from pathlib import Path
import runpy

import pytest


ROOT = Path(__file__).resolve().parents[1]
reviewed_exceptions = runpy.run_path(str(ROOT / "scripts/audit_dependencies.py"))["reviewed_exceptions"]
registry_requirements = runpy.run_path(str(ROOT / "scripts/audit_dependencies.py"))["registry_requirements"]


def policy():
    return json.loads((ROOT / "tests/fixtures/torch-2.10-advisory-policy.json").read_text())


def test_historical_review_is_exact_and_expires_at_its_deadline():
    value = policy()
    reviewed = date.fromisoformat(value["exceptions"][0]["reviewed_on"])
    assert reviewed_exceptions(value, "torch==2.10.0\n", today=reviewed) == ["CVE-2025-3000", "PYSEC-2026-139"]
    expires = date.fromisoformat(value["exceptions"][0]["expires_on"])
    with pytest.raises(ValueError, match="expired"):
        reviewed_exceptions(value, "torch==2.10.0\n", today=expires)


@pytest.mark.parametrize("damage", ["extra_advisory", "missing_owner", "version", "future", "long_review"])
def test_changed_scope_requires_a_new_review(damage):
    value = deepcopy(policy())
    entry = value["exceptions"][0]
    reviewed = date.fromisoformat(entry["reviewed_on"])
    if damage == "extra_advisory":
        value["exceptions"].append({**entry, "id": "unreviewed"})
    elif damage == "missing_owner":
        entry["owner"] = ""
    elif damage == "version":
        entry["version"] = "2.13.0"
    elif damage == "future":
        entry["reviewed_on"] = "2099-01-01"
    else:
        entry["expires_on"] = "2099-01-01"
    with pytest.raises(ValueError):
        reviewed_exceptions(value, "torch==2.10.0\n", today=reviewed)


def test_a_changed_dependency_cannot_reuse_the_exception():
    value = policy()
    with pytest.raises(ValueError, match="dependency changed"):
        reviewed_exceptions(value, "torch==2.13.0\n")


def test_git_source_review_cannot_hide_registry_or_changed_dependencies():
    value = policy()
    entry = {**value["exceptions"][0], "requirement": "example @ git+https://github.com/owner/repo.git@" + "a" * 40}
    value["unindexed_sources"] = [entry]
    requirements = "torch==2.10.0\n" + entry["requirement"] + "\nanyio==4.15.1\n"
    reviewed = date.fromisoformat(entry["reviewed_on"])
    registry, sources = registry_requirements(value, requirements, today=reviewed)
    assert registry == "torch==2.10.0\nanyio==4.15.1\n"
    assert sources == [entry]
    with pytest.raises(ValueError, match="Direct source changed"):
        registry_requirements(value, requirements.replace("a" * 40, "b" * 40), today=reviewed)
    with pytest.raises(ValueError, match="no longer matches"):
        registry_requirements(value, "torch==2.10.0\n", today=reviewed)
    with pytest.raises(ValueError, match="expired"):
        registry_requirements(value, requirements, today=date.fromisoformat(entry["expires_on"]))


def test_no_exception_policy_does_not_suppress_audit_failures(tmp_path, monkeypatch):
    import subprocess
    import sys
    from types import SimpleNamespace

    current = json.loads((ROOT / "advisory-exceptions.json").read_text())
    assert current["exceptions"] == []
    assert reviewed_exceptions(current, "torch==2.13.0\n") == []
    requirements = tmp_path / "dependencies.txt"
    requirements.write_text("torch==2.13.0\n")
    output = tmp_path / "audit.json"
    module = runpy.run_path(str(ROOT / "scripts/audit_dependencies.py"))
    main = module["main"]
    calls = []

    def failed_audit(command, **kwargs):
        calls.append(command)
        assert "--ignore-vuln" not in command
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(sys, "argv", ["audit", "--requirements", str(requirements), "--output", str(output)])
    monkeypatch.setattr(subprocess, "run", failed_audit)
    # Source-review expiry is tested separately; this test targets audit failure propagation.
    monkeypatch.setitem(main.__globals__, "registry_requirements", lambda policy, text: (text, []))
    assert main() == 1
    assert len(calls) == 1
    assert output.with_suffix(".requirements.txt").read_text() == "torch==2.13.0\n"
