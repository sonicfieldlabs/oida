"""Audit a locked export; refuse expired or widened optional Torch exceptions."""
import argparse
from datetime import date
import json
from pathlib import Path
import re
import subprocess


REVIEWED_IDS = {"PYSEC-2026-139", "CVE-2025-3000"}


def validate_review(entry, today):
    reviewed = date.fromisoformat(entry["reviewed_on"])
    expires = date.fromisoformat(entry["expires_on"])
    if not reviewed <= today < expires or not 0 < (expires - reviewed).days <= 30:
        raise ValueError("Advisory review is expired, future-dated or longer than 30 days")
    if any(not entry.get(key) for key in ("owner", "rationale", "sources", "invalidated_by")):
        raise ValueError("Exception requires owner, rationale, sources and invalidation conditions")


def reviewed_exceptions(policy, requirements, *, today=None):
    today = today or date.today()
    if policy.get("contract") != "listening-stack/advisory-exceptions/v1":
        raise ValueError("Unknown advisory exception contract")
    entries = policy.get("exceptions", [])
    if len(entries) != 2 or {entry.get("id") for entry in entries} != REVIEWED_IDS:
        raise ValueError("Only the two explicitly reviewed advisory identities are allowed")
    torch_versions = re.findall(r"^torch==([^ ;\\\r\n]+)", requirements, re.MULTILINE)
    if not torch_versions or set(torch_versions) != {"2.10.0"}:
        raise ValueError("The Torch dependency changed; re-review exceptions before auditing")
    for entry in entries:
        validate_review(entry, today)
        if entry.get("package") != "torch" or entry.get("version") != "2.10.0":
            raise ValueError("Exception package/version does not match the reviewed runtime")
    return sorted(REVIEWED_IDS)


def registry_requirements(policy, requirements, *, today=None):
    """Keep all registry dependencies; separately identify reviewed Git sources."""
    today = today or date.today()
    sources = policy.get("unindexed_sources", [])
    reviewed = {}
    for entry in sources:
        validate_review(entry, today)
        requirement = entry["requirement"]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+ @ git\+https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}", requirement):
            raise ValueError("A source review must identify a complete immutable Git requirement")
        if requirement in reviewed:
            raise ValueError("Duplicate source review")
        reviewed[requirement] = entry
    output, found = [], []
    for line in requirements.splitlines(keepends=True):
        if not line.lstrip().startswith("#") and " @ " in line:
            requirement = line.strip()
            if requirement not in reviewed:
                raise ValueError("Direct source changed or has no current review: " + requirement)
            found.append(requirement)
        else:
            output.append(line)
    if set(found) != set(reviewed):
        raise ValueError("Source review no longer matches the locked graph")
    return "".join(output), sources


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requirements", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    policy_path = Path(__file__).resolve().parents[1] / "advisory-exceptions.json"
    policy = json.loads(policy_path.read_text())
    requirements = args.requirements.read_text()
    ids = reviewed_exceptions(policy, requirements)
    registry, sources = registry_requirements(policy, requirements)
    audit_input = args.output.with_suffix(".requirements.txt")
    audit_input.write_text(registry)
    coverage = {"registry_audit": "pip-audit", "unindexed_sources": sources,
                "source_code_vulnerability_scan": "not_claimed", "transitive_registry_dependencies_retained": True}
    args.output.with_suffix(".source-coverage.json").write_text(json.dumps(coverage, indent=2) + "\n")
    print(json.dumps({"reviewed_exceptions": ids, "policy": str(policy_path)}), flush=True)
    command = ["uvx", "--from", "pip-audit==2.10.1", "pip-audit",
               "--requirement", str(audit_input), "--disable-pip",
               "--progress-spinner=off", "--format", "json", "--output", str(args.output)]
    for identifier in ids:
        command += ["--ignore-vuln", identifier]
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
