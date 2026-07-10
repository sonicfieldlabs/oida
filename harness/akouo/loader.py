from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import jsonschema
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012


DEFAULT_AKOUO_CANDIDATES = [
    Path((os.getenv("OIDA_AKOUO_ROOT") or os.getenv("HMM_AKOUO_ROOT") or os.getenv("AEAR_AKOUO_ROOT"))) if (os.getenv("OIDA_AKOUO_ROOT") or os.getenv("HMM_AKOUO_ROOT") or os.getenv("AEAR_AKOUO_ROOT")) else None,
    Path.home() / "Documents" / "SFL" / "akouo",
    Path.home() / "Documents" / "sfl" / "akouo",
]


def default_akouo_root() -> Path:
    for candidate in DEFAULT_AKOUO_CANDIDATES:
        if candidate and candidate.expanduser().exists():
            return candidate.expanduser().resolve()
    try:
        from akouo_contract import root as installed_root

        packaged = installed_root()
        if packaged.exists():
            return packaged
    except (ImportError, OSError):
        pass
    return Path.home() / "Documents" / "SFL" / "akouo"


class AkouoLoader:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root).expanduser().resolve() if root else default_akouo_root()
        self.schemas_dir = self.root / "schemas"
        self.skills_dir = self.root / "skills"

    def skill_path(self, skill_id: str) -> Path:
        path = self.skills_dir / skill_id / "SKILL.md"
        if not path.exists():
            raise FileNotFoundError(f"AKOUO skill not found: {path}")
        return path

    def schema_path(self, name: str) -> Path:
        filename = name if name.endswith(".json") else f"{name}.schema.json"
        path = self.schemas_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"AKOUO schema not found: {path}")
        return path

    def load_skill(self, skill_id: str) -> str:
        return self.skill_path(skill_id).read_text(encoding="utf-8")

    def load_schema(self, name: str) -> dict[str, Any]:
        return json.loads(self.schema_path(name).read_text(encoding="utf-8"))

    def validate(self, schema_name: str, instance: dict[str, Any]) -> None:
        schema = self.load_schema(schema_name)
        registry = self._schema_registry()
        validator = jsonschema.Draft202012Validator(schema, registry=registry)
        validator.validate(instance)

    def _schema_registry(self) -> Registry:
        resources: list[tuple[str, Resource]] = []
        for path in self.schemas_dir.glob("*.json"):
            schema = json.loads(path.read_text(encoding="utf-8"))
            resources.append((path.name, Resource.from_contents(schema, default_specification=DRAFT202012)))
            if "$id" in schema:
                resources.append((str(schema["$id"]), Resource.from_contents(schema, default_specification=DRAFT202012)))
        return Registry().with_resources(resources)
