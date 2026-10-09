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
    return json.loads((ROOT / "advisory-exceptions.json").read_text())


def test_current_review_is_exact_and_expires_at_its_deadline():
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
    registry, sources = registry_requirements(value, requirements)
    assert registry == "torch==2.10.0\nanyio==4.15.1\n"
    assert sources == [entry]
    with pytest.raises(ValueError, match="Direct source changed"):
        registry_requirements(value, requirements.replace("a" * 40, "b" * 40))
    with pytest.raises(ValueError, match="no longer matches"):
        registry_requirements(value, "torch==2.10.0\n")
    with pytest.raises(ValueError, match="expired"):
        registry_requirements(value, requirements, today=date.fromisoformat(entry["expires_on"]))
