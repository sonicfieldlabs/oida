"""Local model resource estimates for the settings surface.

These are guardrails, not allocators.  Oída never downloads or loads a model
merely to estimate it, and quantization/runtime choices can materially change
actual memory use.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

from oida.reasoning.contracts import ProviderLocality, ReasoningSettings
from oida.reasoning.model_catalog import find_model_spec


def physical_memory_gb() -> float | None:
    value: int | None = None
    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["/usr/sbin/sysctl", "-n", "hw.memsize"],
                check=True,
                capture_output=True,
                text=True,
                timeout=2,
            )
            value = int(result.stdout.strip())
        except (OSError, ValueError, subprocess.SubprocessError):
            value = None
    if value is None:
        try:
            pages = int(os.sysconf("SC_PHYS_PAGES"))
            page_size = int(os.sysconf("SC_PAGE_SIZE"))
            value = pages * page_size
        except (AttributeError, OSError, ValueError):
            value = None
    return round(value / 1_073_741_824, 1) if value and value > 0 else None


def resource_assessment(
    settings: ReasoningSettings,
    *,
    resident_mode: str = "single",
    model_overrides: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    total_ram = physical_memory_gb()
    overrides = dict(model_overrides or {})
    selected: list[dict[str, Any]] = []
    warnings: list[str] = []
    seen: set[tuple[str, str]] = set()

    for role, assignment in settings.roles.items():
        provider = settings.providers.get(assignment.provider_id)
        if provider is None:
            continue
        if provider.locality == ProviderLocality.EXTERNAL:
            if role.value != "conversation" and not settings.allow_external_audio:
                warnings.append(
                    f"{role.value}: {assignment.provider_id} is selected, but external audio sharing is off; Oída will use its local fallback."
                )
            continue
        if role.value == "conversation" and assignment.provider_id == "local_structured":
            continue

        configured_id = assignment.model_id or provider.default_model
        if assignment.provider_id == "oida_moss" and configured_id in overrides:
            configured_id = overrides[configured_id]
        if not configured_id:
            continue
        spec = find_model_spec(assignment.provider_id, configured_id)
        if spec is None and assignment.provider_id == "oida_moss":
            spec = find_model_spec(assignment.provider_id, Path(configured_id).name)
        key = (assignment.provider_id, spec.id if spec else str(configured_id))
        if key in seen:
            for item in selected:
                if (item["provider_id"], item["model_id"]) == key:
                    item["roles"].append(role.value)
            continue
        seen.add(key)
        item = {
            "provider_id": assignment.provider_id,
            "model_id": key[1],
            "name": spec.name if spec else Path(str(configured_id)).name,
            "roles": [role.value],
            "min_ram_gb": spec.min_ram_gb if spec else None,
            "recommended_ram_gb": spec.recommended_ram_gb if spec else None,
            "weight_gb": spec.weight_gb if spec else None,
            "runtime": spec.runtime if spec else "custom_local_checkpoint",
            "integration_status": spec.integration_status if spec else "locally_configured_unknown",
        }
        selected.append(item)

        if spec is not None:
            if sys.platform == "darwin" and spec.platforms and not any(
                value in {"macos-mps", "cpu", "transformers", "mps-runtime-dependent", "custom-python-runtime"}
                for value in spec.platforms
            ):
                warnings.append(
                    f"{spec.name} expects {', '.join(spec.platforms)} and is not an embedded macOS runtime; configure a compatible local host."
                )
            if total_ram is not None and spec.min_ram_gb is not None and spec.min_ram_gb > total_ram:
                warnings.append(
                    f"{spec.name} is estimated to need at least {spec.min_ram_gb:g} GB RAM, above this machine's {total_ram:g} GB."
                )
            if "untested" in spec.integration_status or "unverified" in spec.integration_status:
                warnings.append(
                    f"{spec.name} is configuration support only and has not been executed on this machine."
                )

    estimates = [
        float(item["recommended_ram_gb"])
        for item in selected
        if isinstance(item.get("recommended_ram_gb"), (int, float))
    ]
    mode = "multi" if str(resident_mode).lower() == "multi" else "single"
    estimated_peak = (sum(estimates) if mode == "multi" else max(estimates, default=0.0)) or None
    if estimated_peak is None:
        level = "unknown" if selected else "ok"
    elif total_ram is not None and estimated_peak > total_ram:
        level = "exceeds"
        warnings.append(
            f"Estimated peak model memory is {estimated_peak:g} GB with {mode} residency, above {total_ram:g} GB physical RAM."
        )
    elif total_ram is not None and estimated_peak > total_ram * 0.8:
        level = "warning"
        warnings.append(
            "Estimated peak model memory uses more than 80% of this machine's physical RAM."
        )
    else:
        level = "ok"

    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "physical_ram_gb": total_ram,
        "resident_mode": mode,
        "estimated_peak_ram_gb": round(estimated_peak, 1) if estimated_peak is not None else None,
        "level": level,
        "selected_local_models": selected,
        "warnings": list(dict.fromkeys(warnings)),
        "estimate_note": "Planning estimate only; precision, quantization, KV cache, audio duration, endpoint residency, and runtime overhead change actual use.",
    }
