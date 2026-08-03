from __future__ import annotations

import json
import time

from pydantic import ValidationError

from oida.reasoning.contracts import (
    AnswerBlock,
    EvidenceItem,
    EvidencePacket,
    ModelDescriptor,
    ProviderDescriptor,
    ProviderRequest,
    ProviderResult,
    ReasoningHypothesis,
    ReasoningProfile,
    ReasoningResponse,
)


class DeterministicLocalProvider:
    """Offline structured conversation over an already-filtered evidence packet."""

    provider_id = "local_structured"
    model_id = "oida-deterministic-v1"

    def probe(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            id=self.provider_id,
            name="Oída local structured",
            kind="local_structured",
            locality="local",
            enabled=True,
            available=True,
            authenticated=None,
            capabilities=["text", "structured_output", "offline", "evidence_refs"],
            detail="Deterministic event-grounded fallback; no model or network call.",
        )

    def list_models(self) -> list[ModelDescriptor]:
        return [
            ModelDescriptor(
                id=self.model_id,
                provider_id=self.provider_id,
                name="Oída deterministic structured response",
                capabilities=["text", "structured_output", "offline", "evidence_refs"],
                locality="local",
            )
        ]

    def complete(self, request: ProviderRequest) -> ProviderResult:
        started = time.monotonic()
        if request.provider_id != self.provider_id:
            return self._error(request, f"request targets {request.provider_id!r}, not {self.provider_id!r}", started)
        raw_packet = request.metadata.get("evidence_packet")
        try:
            packet = raw_packet if isinstance(raw_packet, EvidencePacket) else EvidencePacket.model_validate(raw_packet)
        except ValidationError as exc:
            return self._error(request, f"local structured provider requires a valid evidence_packet: {exc}", started)
        raw_profile = request.metadata.get("reasoning_profile")
        try:
            profile = ReasoningProfile.model_validate(raw_profile) if raw_profile else None
        except ValidationError:
            profile = None
        response = self.answer(packet, profile=profile)
        parsed = response.model_dump(mode="json")
        return ProviderResult(
            provider_id=self.provider_id,
            model_id=request.model_id or self.model_id,
            status="ok",
            content=json.dumps(parsed, ensure_ascii=False),
            parsed=parsed,
            latency_ms=round((time.monotonic() - started) * 1000),
            raw_metadata={"deterministic": True, "network_used": False},
        )

    def answer(
        self,
        packet: EvidencePacket,
        *,
        profile: ReasoningProfile | None = None,
    ) -> ReasoningResponse:
        by_kind: dict[str, list[EvidenceItem]] = {}
        for item in packet.items:
            by_kind.setdefault(item.kind, []).append(item)
        question = packet.question.lower()
        blocks: list[AnswerBlock] = []
        anchor_ref = next(
            (item.ref for item in packet.items if item.kind == "event_anchor"),
            packet.items[0].ref if packet.items else "",
        )
        depth = profile.depth.value if profile is not None else "balanced"
        fact_limit = {"brief": 1, "balanced": 3, "deep": 5}.get(depth, 3)
        hypothesis_limit = {"brief": 2, "balanced": 4, "deep": 8}.get(depth, 4)
        uncertainty_limit = {"brief": 3, "balanced": 8, "deep": 12}.get(depth, 8)

        relisten = by_kind.get("relisten", [])[:1]
        if relisten:
            item = relisten[0]
            value = item.value if isinstance(item.value, dict) else {"observation": item.value}
            observation = str(value.get("observation") or "").strip()
            if observation:
                blocks.append(
                    AnswerBlock(
                        kind="answer",
                        text=observation,
                        evidence_refs=[item.ref],
                    )
                )
        if not blocks and any(term in question for term in ("duration", "how long", "length")):
            duration = next((item for item in by_kind.get("feature", []) if item.ref.endswith(":duration_s")), None)
            if duration is not None and isinstance(duration.value, (int, float)):
                blocks.append(
                    AnswerBlock(
                        kind="fact",
                        text=f"The listening event is {float(duration.value):.2f} seconds long.",
                        evidence_refs=[duration.ref],
                    )
                )
            else:
                blocks.append(
                    AnswerBlock(
                        kind="dialogue",
                        text="The available evidence does not include a duration.",
                        evidence_refs=[anchor_ref],
                    )
                )
        elif not blocks and any(term in question for term in ("memory", "similar", "remember", "before")):
            memories = by_kind.get("memory", [])[:3]
            if memories:
                labels = []
                for item in memories:
                    value = item.value if isinstance(item.value, dict) else {}
                    title = str(value.get("title") or item.ref)
                    score = value.get("score")
                    labels.append(f"{title} ({round(float(score) * 100)}% similarity)" if isinstance(score, (int, float)) else title)
                blocks.append(
                    AnswerBlock(
                        kind="fact",
                        text="Related Akousmata traces: " + "; ".join(labels) + ".",
                        evidence_refs=[item.ref for item in memories],
                    )
                )
            else:
                blocks.append(
                    AnswerBlock(
                        kind="dialogue",
                        text="No memory content was included for this turn, so I cannot make a grounded similarity comparison.",
                        evidence_refs=[anchor_ref],
                    )
                )
        elif not blocks and any(term in question for term in ("uncertain", "confidence", "sure", "hypothesis", "guess")):
            uncertainty = by_kind.get("uncertainty", [])[:4]
            if uncertainty:
                blocks.append(
                    AnswerBlock(
                        kind="fact",
                        text="The recorded uncertainties are: " + "; ".join(str(item.value) for item in uncertainty) + ".",
                        evidence_refs=[item.ref for item in uncertainty],
                    )
                )
            else:
                blocks.append(
                    AnswerBlock(
                        kind="dialogue",
                        text="No explicit uncertainty notes were included in this packet.",
                        evidence_refs=[anchor_ref],
                    )
                )
        elif not blocks and any(term in question for term in ("route", "skill", "analysis")):
            routes = by_kind.get("route", [])[:5]
            if routes:
                blocks.append(
                    AnswerBlock(
                        kind="fact",
                        text="The route summaries report: " + "; ".join(str(item.value) for item in routes) + ".",
                        evidence_refs=[item.ref for item in routes],
                    )
                )
            else:
                blocks.append(
                    AnswerBlock(
                        kind="dialogue",
                        text="No route summary was included in the available evidence.",
                        evidence_refs=[anchor_ref],
                    )
                )
        elif not blocks:
            summary = _preferred_summary(by_kind.get("summary", []))
            if summary is not None:
                blocks.append(AnswerBlock(kind="answer", text=str(summary.value), evidence_refs=[summary.ref]))
            facts = [
                item
                for item in by_kind.get("claim", [])
                if item.category in {"heard", "measured", "inferred"}
            ][:fact_limit]
            if facts:
                blocks.append(
                    AnswerBlock(
                        kind="fact",
                        text="Attributed details: " + "; ".join(str(item.value) for item in facts) + ".",
                        evidence_refs=[item.ref for item in facts],
                    )
                )
            if not blocks:
                blocks.append(
                    AnswerBlock(
                        kind="dialogue",
                        text="The filtered listening packet does not contain enough evidence to answer that question.",
                        evidence_refs=[anchor_ref],
                    )
                )

        hypotheses = []
        for item in by_kind.get("claim", []):
            if item.category not in {"inferred", "interpreted", "speculative"}:
                continue
            hypotheses.append(
                ReasoningHypothesis(
                    statement=str(item.value),
                    confidence=_confidence(item.confidence),
                    evidence_refs=[item.ref],
                )
            )
            if len(hypotheses) >= hypothesis_limit:
                break
        uncertainties = [
            str(item.value) for item in by_kind.get("uncertainty", [])[:uncertainty_limit]
        ]
        suggested = []
        if packet.items and (
            profile is None or profile.initiative.value != "answer_only"
        ):
            suggested = [
                "Which parts are measured versus inferred?",
                "What remains uncertain in this listening event?",
            ]
            if profile is not None and profile.initiative.value == "suggest_followups":
                suggested = suggested[:1]
        if profile is not None and profile.language.lower() not in {"auto", "en", "en-us", "english"}:
            uncertainties.append(
                "The deterministic local fallback cannot reliably translate its fixed response templates; configure a local reasoning model for full language control."
            )
        return ReasoningResponse(
            answer_blocks=blocks,
            hypotheses=hypotheses,
            uncertainties=uncertainties,
            suggested_questions=suggested,
        )

    def _error(self, request: ProviderRequest, message: str, started: float) -> ProviderResult:
        return ProviderResult(
            provider_id=request.provider_id,
            model_id=request.model_id,
            status="error",
            latency_ms=round((time.monotonic() - started) * 1000),
            error=message[:4000],
            raw_metadata={"deterministic": True, "network_used": False},
        )


def _preferred_summary(items: list[EvidenceItem]) -> EvidenceItem | None:
    for suffix in (":short_summary", ":detailed_summary", ":title"):
        match = next((item for item in items if item.ref.endswith(suffix)), None)
        if match is not None:
            return match
    return items[0] if items else None


def _confidence(value: str | None) -> str:
    return value if value in {"high", "medium", "low", "undetermined"} else "undetermined"


# Public role-oriented name; retain the descriptive implementation name for
# callers that want to make the no-model behavior explicit.
LocalStructuredProvider = DeterministicLocalProvider
