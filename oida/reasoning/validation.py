from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from oida.reasoning.contracts import EvidencePacket, ProviderResult, ReasoningResponse


class ResponseValidationError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


class ResponseValidator:
    """Validate provider output against the packet's actual evidence refs."""

    def validate(
        self,
        payload: str | dict[str, Any] | ProviderResult,
        *,
        packet: EvidencePacket,
        allow_targeted_relisten: bool = True,
    ) -> ReasoningResponse:
        value = _provider_payload(payload)
        try:
            response = ReasoningResponse.model_validate(value)
        except ValidationError as exc:
            errors = [
                f"{'.'.join(str(part) for part in item['loc']) or 'response'}: {item['msg']}"
                for item in exc.errors(include_url=False)
            ]
            raise ResponseValidationError(errors) from exc

        allowed_refs = {item.ref for item in packet.items}
        errors: list[str] = []
        for index, block in enumerate(response.answer_blocks):
            if not block.evidence_refs:
                errors.append(f"answer_blocks[{index}] requires at least one evidence ref")
            errors.extend(_invalid_refs(block.evidence_refs, allowed_refs, f"answer_blocks[{index}]"))
            if _contains_private_locator(block.text):
                errors.append(f"answer_blocks[{index}] contains a local path or raw-audio locator")
        for index, hypothesis in enumerate(response.hypotheses):
            if not hypothesis.evidence_refs:
                errors.append(f"hypotheses[{index}] requires at least one evidence ref")
            errors.extend(_invalid_refs(hypothesis.evidence_refs, allowed_refs, f"hypotheses[{index}]"))
            if _contains_private_locator(hypothesis.statement):
                errors.append(f"hypotheses[{index}] contains a local path or raw-audio locator")
        if response.requested_action is not None and not allow_targeted_relisten:
            errors.append("targeted re-listen is not allowed for this turn")
        for index, value in enumerate([*response.uncertainties, *response.suggested_questions]):
            if _contains_private_locator(value):
                errors.append(f"response text item {index} contains a local path or raw-audio locator")
        if errors:
            raise ResponseValidationError(errors)
        return response

    def validate_provider_result(
        self,
        result: ProviderResult,
        *,
        packet: EvidencePacket,
        allow_targeted_relisten: bool = True,
    ) -> ReasoningResponse:
        if result.status.value != "ok":
            raise ResponseValidationError([result.error or f"provider returned {result.status.value}"])
        return self.validate(
            result,
            packet=packet,
            allow_targeted_relisten=allow_targeted_relisten,
        )


def _provider_payload(payload: str | dict[str, Any] | ProviderResult) -> dict[str, Any]:
    if isinstance(payload, ProviderResult):
        if payload.parsed is not None:
            return payload.parsed
        payload = payload.content or ""
    if isinstance(payload, dict):
        return payload
    text = str(payload or "").strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().lower() in {"```", "```json"}:
            text = "\n".join(lines[1:-1]).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ResponseValidationError(["provider output is not a JSON object"]) from exc
    if not isinstance(value, dict):
        raise ResponseValidationError(["provider output must be a JSON object"])
    return value


def _invalid_refs(values: list[str], allowed: set[str], label: str) -> list[str]:
    errors = []
    for ref in values:
        if ref not in allowed:
            errors.append(f"{label} cites unknown evidence ref {ref!r}")
    return errors


_PRIVATE_LOCATOR_PATTERNS = (
    re.compile(r"\bfile://", re.IGNORECASE),
    re.compile(r"\b[a-z][a-z0-9+.-]*://", re.IGNORECASE),
    re.compile(r"\bdata:(?:audio|application/octet-stream)\b", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9._-])/(?!/)[^\s'\"<>)]*[^\s'\"<>).,;:!?]"),
    re.compile(r"(?:^|[\s'\"(])~/(?:[^\s'\"<>)]*)"),
    re.compile(r"\b[A-Za-z]:\\[^\s'\"<>]+", re.IGNORECASE),
    re.compile(r"\\\\[^\\\s'\"<>]+\\[^\s'\"<>]+"),
    re.compile(r"\b(?:audio_url|data_ref\.uri)\b", re.IGNORECASE),
)


def _contains_private_locator(value: str) -> bool:
    return any(pattern.search(value) for pattern in _PRIVATE_LOCATOR_PATTERNS)
