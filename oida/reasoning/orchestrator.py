from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from oida.conversation import ConversationStore
from oida.contracts import new_id, now_iso
from oida.listening_identity import ListeningIdentitySnapshot, ListeningIdentityStore
from oida.memory import AkousmataStore
from oida.reasoning.contracts import (
    EvidenceItem,
    EvidencePacket,
    ModelRole,
    ProviderLocality,
    ProviderResult,
    ReasoningProfile,
    ReasoningResponse,
    ReasoningSettings,
)
from oida.reasoning.deterministic import LocalStructuredProvider
from oida.reasoning.evidence import (
    EvidencePacketBuilder,
    covenant_blocks_untyped_prose,
    safe_external_text,
)
from oida.reasoning.prompts import CompiledPrompt, PromptCompiler, trusted_route_instructions
from oida.reasoning.providers.base import endpoint_locality
from oida.reasoning.registry import ProviderRegistry, build_provider_registry
from oida.reasoning.secrets import SecretStore
from oida.reasoning.settings import ReasoningSettingsStore
from oida.reasoning.validation import ResponseValidationError, ResponseValidator
from oida.relisten import RelistenUnavailable, TargetedRelistener


RegistryFactory = Callable[[ReasoningSettings], ProviderRegistry]


@dataclass(frozen=True)
class TurnOptions:
    provider_id: str | None = None
    model_id: str | None = None
    profile_id: str | None = None
    conversation_id: str | None = None
    comparison_events: list[dict[str, Any]] = field(default_factory=list)
    include_memory: bool = True
    include_transcript: bool | None = None
    include_memory_content: bool | None = None
    allow_targeted_relisten: bool | None = None


@dataclass
class _TurnContext:
    event: dict[str, Any]
    question: str
    options: TurnOptions
    settings: ReasoningSettings
    provider_id: str
    requested_provider_id: str
    model_id: str | None
    locality: ProviderLocality
    profile: ReasoningProfile
    packet: EvidencePacket
    compiled: CompiledPrompt
    route_instructions: str | None
    listening_identity: str
    listening_identity_snapshot: ListeningIdentitySnapshot
    memory_context: list[dict[str, Any]]
    include_transcript: bool
    include_memory_content: bool
    conversation_prepared: dict[str, Any]
    turn_id: str
    forced_fallback_reason: str | None = None
    relisten: dict[str, Any] | None = None


@dataclass
class _PreparedTurn:
    context: _TurnContext
    expires_monotonic: float
    stage: int = 0


class ReasoningOrchestrator:
    """Own prompt/evidence policy, provider execution, fallback, and commit.

    Provider adapters are deliberately dumb one-turn transports.  This layer
    is the only place that can choose a provider, repair once, run a single
    local targeted re-listen, or persist a final turn.
    """

    def __init__(
        self,
        *,
        settings_store: ReasoningSettingsStore,
        secret_store: SecretStore,
        conversations: ConversationStore,
        memory: AkousmataStore,
        listening_identity_store: ListeningIdentityStore | None = None,
        relistener: TargetedRelistener | None = None,
        registry_factory: RegistryFactory | None = None,
        prepare_ttl_seconds: float = 600.0,
    ) -> None:
        self.settings_store = settings_store
        self.secret_store = secret_store
        self.conversations = conversations
        self.memory = memory
        self.listening_identity_store = listening_identity_store
        self.relistener = relistener
        self.packet_builder = EvidencePacketBuilder()
        self.prompt_compiler = PromptCompiler()
        self.validator = ResponseValidator()
        self.local_provider = LocalStructuredProvider()
        self.registry_factory = registry_factory or (
            lambda settings: build_provider_registry(
                settings,
                secret_store=self.secret_store,
                local_provider=self.local_provider,
            )
        )
        self.prepare_ttl_seconds = max(60.0, float(prepare_ttl_seconds))
        self._prepared: dict[str, _PreparedTurn] = {}
        self._prepared_lock = threading.RLock()

    def ask(
        self,
        *,
        event: dict[str, Any],
        question: str,
        options: TurnOptions | None = None,
    ) -> dict[str, Any]:
        context = self._context(event=event, question=question, options=options or TurnOptions())
        response, execution = self._execute(context)
        return self._commit_context(context, response, execution=execution)

    def prepare(
        self,
        *,
        event: dict[str, Any],
        question: str,
        options: TurnOptions | None = None,
    ) -> dict[str, Any]:
        context = self._context(event=event, question=question, options=options or TurnOptions())
        # prepare/commit is for the model already hosting the MCP tool.  A
        # local deterministic selection should use the daemon-managed ask path
        # instead of exporting a packet to an unselected host.
        if context.provider_id == "local_structured":
            raise ValueError("the active reasoner is daemon-managed; use oida_ask instead")
        token = self._issue_token(context, stage=0)
        return self._prepared_payload(token, context, stage=0)

    def commit_prepared(
        self,
        *,
        token: str,
        response: dict[str, Any] | str,
    ) -> dict[str, Any]:
        prepared = self._consume_token(token)
        context = prepared.context
        try:
            validated = self.validator.validate(
                response,
                packet=context.packet,
                allow_targeted_relisten=prepared.stage == 0 and self._relisten_allowed(context),
            )
        except ResponseValidationError:
            # A host-managed prepare/commit turn has no safe automatic way to
            # re-prompt the already-running host.  Reject it visibly rather
            # than persisting unvalidated output.
            raise

        if validated.requested_action is not None and prepared.stage == 0:
            sidecar, relisten_error = self._run_relisten(context, validated)
            if sidecar is not None:
                context.relisten = sidecar
                context.packet = _packet_with_relisten(context.packet, sidecar)
                context.compiled = self.prompt_compiler.compile(
                    packet=context.packet,
                    profile=context.profile,
                    route_instructions=context.route_instructions,
                    listening_identity=context.listening_identity,
                    conversation_history=self._history_for_packet(
                        context.options.conversation_id,
                        context.packet,
                    ),
                )
                next_token = self._issue_token(context, stage=1)
                payload = self._prepared_payload(next_token, context, stage=1)
                payload.update(
                    {
                        "requires_followup": True,
                        "relisten": _public_relisten(sidecar),
                    }
                )
                return payload
            validated = _without_action(
                validated,
                relisten_error or "Targeted local re-listening was unavailable.",
            )
        elif validated.requested_action is not None:
            raise ResponseValidationError(["a second targeted re-listen is not permitted"])

        execution = {
            "selected_provider_id": context.provider_id,
            "provider_id": context.provider_id,
            "model_id": context.model_id,
            "locality": context.locality.value,
            "repaired": False,
            "attempts": 0,
            "host_managed": True,
            "fallback": None,
        }
        return self._commit_context(context, validated, execution=execution)

    def _context(self, *, event: dict[str, Any], question: str, options: TurnOptions) -> _TurnContext:
        normalized_question = " ".join(str(question or "").split()).strip()
        if not normalized_question:
            raise ValueError("conversation question is required")
        if len(normalized_question) > 16_000:
            raise ValueError("conversation question is too long")
        if not isinstance(event, dict) or not event.get("id"):
            raise ValueError("conversation requires a listening event")
        comparisons = list(options.comparison_events or [])
        if len(comparisons) > 3:
            raise ValueError("at most three comparison events may be attached")

        settings = self.settings_store.load()
        assignment = settings.roles[ModelRole.CONVERSATION]
        requested_provider = str(options.provider_id or assignment.provider_id or "local_structured")
        if options.model_id is not None:
            model_id = options.model_id
        elif options.provider_id is not None and options.provider_id != assignment.provider_id:
            model_id = None
        else:
            model_id = assignment.model_id
        profile_id = options.profile_id or settings.active_profile_id
        if profile_id not in settings.profiles:
            raise ValueError(f"unknown reasoning profile: {profile_id}")
        profile = settings.profiles[profile_id]
        incognito = str(event.get("privacy_mode") or "") == "incognito"

        forced_fallback: str | None = None
        selected_provider = requested_provider
        configured = settings.providers.get(requested_provider)
        if incognito and requested_provider != "local_structured":
            selected_provider = "local_structured"
            model_id = None
            forced_fallback = "Incognito mode forced local-only reasoning and disabled persistence."
        elif requested_provider != "local_structured" and (configured is None or not configured.enabled):
            selected_provider = "local_structured"
            model_id = None
            forced_fallback = f"The selected provider {requested_provider!r} is not explicitly enabled."

        locality = _provider_locality(selected_provider, model_id, settings)
        memory_context = self.memory.similar_to_event(event, limit=3) if options.include_memory else []
        include_transcript = bool(
            _context_value(options.include_transcript, settings.include_transcript)
        ) and not incognito
        include_memory_content = bool(
            _context_value(options.include_memory_content, settings.include_memory_content)
        ) and not incognito
        packet = self.packet_builder.build(
            event=event,
            question=normalized_question,
            comparison_events=comparisons,
            memory_context=memory_context,
            include_transcript=include_transcript,
            include_memory_content=include_memory_content,
        )
        history = [] if incognito else self._history_for_packet(options.conversation_id, packet)
        route_instructions = trusted_route_instructions(event)
        listening_identity_snapshot = self._listening_identity_snapshot()
        listening_identity = listening_identity_snapshot.text.strip()
        compiled = self.prompt_compiler.compile(
            packet=packet,
            profile=profile,
            route_instructions=route_instructions,
            listening_identity=listening_identity,
            conversation_history=history,
        )
        conversation_prepared = self.conversations.prepare(
            event=event,
            conversation_id=options.conversation_id,
        )
        return _TurnContext(
            event=event,
            question=normalized_question,
            options=options,
            settings=settings,
            provider_id=selected_provider,
            requested_provider_id=requested_provider,
            model_id=model_id,
            locality=locality,
            profile=profile,
            packet=packet,
            compiled=compiled,
            route_instructions=route_instructions,
            listening_identity=listening_identity,
            listening_identity_snapshot=listening_identity_snapshot,
            memory_context=memory_context,
            include_transcript=include_transcript,
            include_memory_content=include_memory_content,
            conversation_prepared=conversation_prepared,
            turn_id=new_id("turn"),
            forced_fallback_reason=forced_fallback,
        )

    def _listening_identity_snapshot(self) -> ListeningIdentitySnapshot:
        if self.listening_identity_store is None:
            return ListeningIdentitySnapshot.empty()
        try:
            return self.listening_identity_store.snapshot()
        except (OSError, ValueError):
            # A malformed optional identity must not make listening or
            # grounded conversation unavailable. The editor endpoint still
            # exposes the file error so the operator can repair it.
            return ListeningIdentitySnapshot.empty()

    def _execute(self, context: _TurnContext) -> tuple[ReasoningResponse, dict[str, Any]]:
        if context.forced_fallback_reason:
            response = self.local_provider.answer(context.packet, profile=context.profile)
            return response, self._fallback_execution(context, context.forced_fallback_reason, attempts=0)

        registry = self.registry_factory(context.settings)
        response, result, repaired, errors, attempts = self._complete_with_repair(
            registry=registry,
            context=context,
            compiled=context.compiled,
            allow_targeted_relisten=self._relisten_allowed(context),
        )
        if response is None:
            reason = errors[-1] if errors else "The selected provider returned no valid structured response."
            local = self.local_provider.answer(context.packet, profile=context.profile)
            return local, self._fallback_execution(context, reason, attempts=attempts)

        execution = {
            "selected_provider_id": context.requested_provider_id,
            "provider_id": context.provider_id,
            "model_id": result.model_id if result is not None else context.model_id,
            "locality": context.locality.value,
            "repaired": repaired,
            "attempts": attempts,
            "host_managed": False,
            "fallback": None,
        }
        if response.requested_action is not None:
            sidecar, relisten_error = self._run_relisten(context, response)
            if sidecar is None:
                response = _without_action(
                    response,
                    relisten_error or "Targeted local re-listening was unavailable.",
                )
            else:
                context.relisten = sidecar
                context.packet = _packet_with_relisten(context.packet, sidecar)
                context.compiled = self.prompt_compiler.compile(
                    packet=context.packet,
                    profile=context.profile,
                    route_instructions=context.route_instructions,
                    listening_identity=context.listening_identity,
                    conversation_history=self._history_for_packet(
                        context.options.conversation_id,
                        context.packet,
                    ),
                )
                response, final_result, final_repaired, errors, final_attempts = self._complete_with_repair(
                    registry=registry,
                    context=context,
                    compiled=context.compiled,
                    allow_targeted_relisten=False,
                    repair_allowed=not repaired,
                )
                execution["attempts"] = int(execution["attempts"]) + final_attempts
                execution["repaired"] = bool(execution["repaired"] or final_repaired)
                if response is not None:
                    execution["model_id"] = (
                        final_result.model_id if final_result is not None else execution["model_id"]
                    ) or execution["model_id"]
                else:
                    response = self.local_provider.answer(context.packet, profile=context.profile)
                    execution = self._fallback_execution(
                        context,
                        "The selected provider failed after the local re-listen: "
                        + (errors[-1] if errors else "invalid structured response"),
                        attempts=int(execution["attempts"]),
                    )
        return response, execution

    def _complete_with_repair(
        self,
        *,
        registry: ProviderRegistry,
        context: _TurnContext,
        compiled: CompiledPrompt,
        allow_targeted_relisten: bool,
        repair_allowed: bool = True,
    ) -> tuple[ReasoningResponse | None, ProviderResult | None, bool, list[str], int]:
        errors: list[str] = []
        result = registry.complete(self._provider_request(context, compiled, allow_relisten=allow_targeted_relisten))
        try:
            return (
                self.validator.validate_provider_result(
                    result,
                    packet=context.packet,
                    allow_targeted_relisten=allow_targeted_relisten,
                ),
                result,
                False,
                errors,
                1,
            )
        except ResponseValidationError as exc:
            errors.extend(exc.errors)

        if not repair_allowed:
            return None, result, False, errors, 1

        repair = self.prompt_compiler.repair_prompt(
            compiled=compiled,
            invalid_output=result.content or result.error or "",
            errors=errors,
        )
        repaired_result = registry.complete(
            self._provider_request(context, repair, allow_relisten=allow_targeted_relisten)
        )
        try:
            return (
                self.validator.validate_provider_result(
                    repaired_result,
                    packet=context.packet,
                    allow_targeted_relisten=allow_targeted_relisten,
                ),
                repaired_result,
                True,
                errors,
                2,
            )
        except ResponseValidationError as exc:
            errors.extend(exc.errors)
            return None, repaired_result, True, errors, 2

    def _provider_request(
        self,
        context: _TurnContext,
        compiled: CompiledPrompt,
        *,
        allow_relisten: bool,
    ):
        effort = {"brief": "low", "balanced": "medium", "deep": "high"}.get(
            context.profile.depth.value,
            "medium",
        )
        return compiled.provider_request(
            provider_id=context.provider_id,
            model_id=context.model_id,
            metadata={
                "evidence_packet": context.packet.model_dump(mode="json"),
                "reasoning_effort": effort,
                "reasoning_profile": context.profile.model_dump(mode="json"),
                "allow_targeted_relisten": allow_relisten,
            },
        )

    def _run_relisten(
        self,
        context: _TurnContext,
        response: ReasoningResponse,
    ) -> tuple[dict[str, Any] | None, str | None]:
        if not self._relisten_allowed(context):
            return None, "Targeted local re-listening is disabled for this turn."
        if self.relistener is None:
            return None, "The local audio re-listening engine is unavailable."
        action = response.requested_action
        if action is None:
            return None, None
        conversation = context.conversation_prepared["conversation"]
        try:
            sidecar = self.relistener.run(
                event=context.event,
                question=action.question,
                conversation_id=str(conversation["id"]),
                turn_id=context.turn_id,
                model_id=context.settings.roles[ModelRole.TARGETED_RELISTEN].model_id,
                time_range=action.time_range,
                allow_speech_content=context.packet.permissions.transcript_included,
                parent_question=context.question,
                listening_identity_snapshot=context.listening_identity_snapshot,
            )
            sidecar = {
                **sidecar,
                # MOSS observations are free-form and may incidentally contain
                # spoken words. Transcript permission is therefore the only
                # structural gate that lets the observation enter another
                # prompt, response, or durable conversation.
                "observation_shared_to_reasoner": bool(context.include_transcript),
            }
            return sidecar, None
        except RelistenUnavailable as exc:
            return None, str(exc)

    def _relisten_allowed(self, context: _TurnContext) -> bool:
        requested = context.options.allow_targeted_relisten
        if requested is None:
            requested = context.settings.allow_targeted_relisten
        return bool(requested)

    def _commit_context(
        self,
        context: _TurnContext,
        response: ReasoningResponse,
        *,
        execution: dict[str, Any],
    ) -> dict[str, Any]:
        response = response.model_copy(update={"requested_action": None})
        response_payload = response.model_dump(mode="json")
        fallback = execution.get("fallback")
        identity_applied = (
            context.listening_identity_snapshot.active
            and (
                bool(execution.get("host_managed"))
                or execution.get("provider_id") != "local_structured"
            )
        )
        identity_block = context.listening_identity_snapshot.event_block(
            application="conversation_prompt" if identity_applied else (
                "available_not_applied" if context.listening_identity_snapshot.active else "inactive"
            ),
            applied_to=["grounded_conversation"] if identity_applied else [],
        )
        turn = {
            "id": context.turn_id,
            "created_at": now_iso(),
            "question": context.question,
            "answer": response.answer,
            **response_payload,
            "known_facts": [
                block.text for block in response.answer_blocks if block.kind in {"fact", "answer"}
            ],
            "uncertainty_notes": list(response.uncertainties),
            "evidence": [_legacy_evidence(item) for item in context.packet.items],
            "memory_context": [_memory_context_item(item) for item in context.memory_context],
            "provider": execution["provider_id"],
            "reasoner": {
                "provider_id": execution["provider_id"],
                "selected_provider_id": execution["selected_provider_id"],
                "model_id": execution.get("model_id"),
                "locality": execution["locality"],
                "repaired": bool(execution.get("repaired")),
                "host_managed": bool(execution.get("host_managed")),
                "attempts": int(execution.get("attempts") or 0),
            },
            "fallback": fallback,
            "relisten": _public_relisten(context.relisten) if context.relisten else None,
            "remote_model": {
                "enabled": execution["provider_id"] != "local_structured",
                "requested": context.requested_provider_id != "local_structured",
                "provider": context.requested_provider_id,
                "note": (
                    fallback.get("note")
                    if isinstance(fallback, dict)
                    else "The selected reasoner returned a validated event-grounded response."
                ),
            },
            "audit": {
                "evidence_contract": context.packet.contract,
                "evidence_refs": [item.ref for item in context.packet.items],
                "prompt_hash": context.compiled.prompt_hash,
                "listening_identity": identity_block,
                "profile_id": context.profile.id,
                "comparison_event_ids": list(context.packet.comparison_event_ids),
                "transcript_included": context.packet.permissions.transcript_included,
                "memory_content_included": context.packet.permissions.memory_content_included,
                "raw_audio_external": False,
                "targeted_relisten_count": 1 if context.relisten else 0,
                "relisten_observation_shared_to_reasoner": bool(
                    context.relisten
                    and context.relisten.get("observation_shared_to_reasoner")
                ),
            },
        }
        prepared = context.conversation_prepared
        result = self.conversations.append_turn(
            event=context.event,
            stored_event=prepared["stored_event"],
            turn=turn,
            conversation=prepared["conversation"],
            persistent=bool(prepared["persistent"]),
            raw_audio_policy=str(prepared["raw_audio_policy"]),
            comparison_event_ids=list(context.packet.comparison_event_ids),
            comparison_events=[
                _redacted_comparison(value) for value in context.options.comparison_events
            ],
        )
        result["forbidden_topics_triggered"] = []
        return result

    def _fallback_execution(
        self,
        context: _TurnContext,
        reason: str,
        *,
        attempts: int,
    ) -> dict[str, Any]:
        note = _bounded_error(reason)
        return {
            "selected_provider_id": context.requested_provider_id,
            "provider_id": "local_structured",
            "model_id": self.local_provider.model_id,
            "locality": "local",
            "repaired": attempts > 1,
            "attempts": attempts,
            "host_managed": False,
            "fallback": {
                "used": True,
                "from_provider_id": context.requested_provider_id,
                "to_provider_id": "local_structured",
                "note": note,
            },
        }

    def _conversation_history(
        self,
        conversation_id: str | None,
        *,
        allow_transcript: bool,
        allow_memory_content: bool,
    ) -> list[dict[str, str]]:
        if not conversation_id:
            return []
        try:
            conversation = self.conversations.get(conversation_id)
        except (FileNotFoundError, ValueError):
            return []
        history: list[dict[str, str]] = []
        for turn in list(conversation.get("turns") or [])[-6:]:
            if not isinstance(turn, dict):
                continue
            audit = turn.get("audit") if isinstance(turn.get("audit"), dict) else None
            if audit is None and (not allow_transcript or not allow_memory_content):
                # Legacy turns have no provenance flags. Do not replay them
                # across a stricter current disclosure boundary.
                continue
            if audit is not None and (
                (bool(audit.get("transcript_included")) and not allow_transcript)
                or (bool(audit.get("memory_content_included")) and not allow_memory_content)
            ):
                continue
            question = str(turn.get("question") or "").strip()
            answer = str(turn.get("answer") or "").strip()
            if question:
                history.append({"role": "user", "content": question})
            if answer:
                history.append({"role": "assistant", "content": answer})
        return history

    def _history_for_packet(
        self,
        conversation_id: str | None,
        packet: EvidencePacket,
    ) -> list[dict[str, str]]:
        if covenant_blocks_untyped_prose(packet.covenant):
            return []
        return self._conversation_history(
            conversation_id,
            allow_transcript=packet.permissions.transcript_included,
            allow_memory_content=packet.permissions.memory_content_included,
        )

    def _issue_token(self, context: _TurnContext, *, stage: int) -> str:
        token = secrets.token_urlsafe(32)
        with self._prepared_lock:
            self._expire_tokens_locked()
            self._prepared[token] = _PreparedTurn(
                context=context,
                stage=stage,
                expires_monotonic=time.monotonic() + self.prepare_ttl_seconds,
            )
        return token

    def _consume_token(self, token: str) -> _PreparedTurn:
        normalized = str(token or "").strip()
        with self._prepared_lock:
            self._expire_tokens_locked()
            prepared = self._prepared.pop(normalized, None)
        if prepared is None:
            raise ValueError("prepare token is invalid, expired, or already used")
        return prepared

    def _expire_tokens_locked(self) -> None:
        now = time.monotonic()
        self._prepared = {
            token: prepared
            for token, prepared in self._prepared.items()
            if prepared.expires_monotonic > now
        }

    def _prepared_payload(self, token: str, context: _TurnContext, *, stage: int) -> dict[str, Any]:
        return {
            "version": "0.2",
            "mode": "host_managed_reasoning",
            "prepare_token": token,
            "expires_in_seconds": round(self.prepare_ttl_seconds),
            "stage": stage,
            "conversation_id": context.conversation_prepared["conversation"]["id"],
            "event_id": context.event.get("id"),
            "provider_id": context.requested_provider_id,
            "model_id": context.model_id,
            "system_prompt": context.compiled.system_prompt,
            "user_prompt": context.compiled.user_prompt,
            "evidence_packet": context.packet.model_dump(mode="json"),
            "response_schema": context.compiled.response_schema,
            "prompt_hash": context.compiled.prompt_hash,
            "raw_audio_included": False,
        }


def _provider_locality(
    provider_id: str,
    model_id: str | None,
    settings: ReasoningSettings,
) -> ProviderLocality:
    if provider_id in {"local_structured", "oida_moss"}:
        return ProviderLocality.LOCAL
    configured = settings.providers.get(provider_id)
    if provider_id == "ollama":
        if str(model_id or "").lower().endswith("cloud"):
            return ProviderLocality.EXTERNAL
        if configured and configured.base_url:
            try:
                return ProviderLocality(endpoint_locality(configured.base_url))
            except ValueError:
                return ProviderLocality.UNKNOWN
        return ProviderLocality.LOCAL
    if configured and configured.base_url and configured.kind.value in {
        "openai_compatible",
        "google",
    }:
        try:
            return ProviderLocality(endpoint_locality(configured.base_url))
        except ValueError:
            return ProviderLocality.UNKNOWN
    # Host CLIs and OpenCode can use an upstream subscription/model even when
    # their local transport is loopback. Treat their evidence boundary as
    # external/unknown; raw audio is still never included.
    if provider_id in {"codex", "claude", "hermes", "openclaw", "opencode"}:
        return ProviderLocality.UNKNOWN
    return configured.locality if configured else ProviderLocality.UNKNOWN


def _packet_with_relisten(packet: EvidencePacket, sidecar: dict[str, Any]) -> EvidencePacket:
    ref = f"relisten:{sidecar.get('id')}:observation:0"
    shared = bool(sidecar.get("observation_shared_to_reasoner"))
    observation = (
        safe_external_text(sidecar.get("observation"), limit=12_000)
        if shared
        else "A local targeted pass ran, but its free-form observation was not shared with this reasoner."
    )
    if not observation:
        observation = "The local targeted pass returned no shareable text."
    limitations = [
        text
        for value in list(sidecar.get("limitations") or [])[:16]
        if (text := safe_external_text(value, limit=2000))
    ]
    model = safe_external_text(sidecar.get("model"), limit=255)
    item = EvidenceItem(
        ref=ref,
        kind="relisten",
        value={
            "observation": observation,
            "limitations": limitations,
            "model": model,
            "time_range": sidecar.get("time_range"),
        },
        event_id=packet.primary_event_id,
        category="heard",
        source="local_moss_targeted_relisten",
    )
    return packet.model_copy(update={"items": [*packet.items, item]})


def _without_action(response: ReasoningResponse, uncertainty: str) -> ReasoningResponse:
    values = list(response.uncertainties)
    if uncertainty and uncertainty not in values:
        values.append(_bounded_error(uncertainty))
    return response.model_copy(update={"requested_action": None, "uncertainties": values})


def _legacy_evidence(item: EvidenceItem) -> dict[str, str]:
    value = item.value if isinstance(item.value, str) else json.dumps(item.value, ensure_ascii=False, sort_keys=True)
    return {"kind": item.kind, "label": item.ref, "value": value[:12_000]}


def _memory_context_item(item: dict[str, Any]) -> dict[str, Any]:
    trace = item.get("trace") if isinstance(item.get("trace"), dict) else item
    return {
        "trace_id": trace.get("id"),
        "title": trace.get("title"),
        "score": item.get("score"),
        "basis": item.get("basis"),
    }


def _public_relisten(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not value:
        return None
    public = {
        key: value.get(key)
        for key in (
            "contract",
            "id",
            "base_event_id",
            "segment_ref",
            "question",
            "parent_question",
            "time_range",
            "engine",
            "model",
            "observation",
            "limitations",
            "created_at",
            "sha256",
            "observation_shared_to_reasoner",
        )
    }
    for key, limit in (
        ("contract", 120),
        ("id", 255),
        ("base_event_id", 255),
        ("segment_ref", 255),
        ("question", 4000),
        ("parent_question", 16_000),
        ("engine", 255),
        ("model", 255),
        ("observation", 12_000),
        ("created_at", 120),
        ("sha256", 160),
    ):
        public[key] = safe_external_text(public.get(key), limit=limit)
    public["limitations"] = [
        text
        for item in list(public.get("limitations") or [])[:16]
        if (text := safe_external_text(item, limit=2000))
    ]
    identity = value.get("listening_identity")
    if isinstance(identity, dict):
        public["listening_identity"] = {
            key: identity.get(key)
            for key in (
                "contract",
                "filename",
                "active",
                "sha256",
                "truncated",
                "application",
                "applied_to",
                "content_included",
                "role",
            )
        }
    if not value.get("observation_shared_to_reasoner"):
        public["observation"] = None
        public["observation_withheld"] = True
    return public


def _redacted_comparison(event: dict[str, Any]) -> dict[str, Any]:
    # ConversationStore has the authoritative redaction path. This reduced
    # representation avoids duplicating arbitrary event internals here.
    aggregate = event.get("aggregate") if isinstance(event.get("aggregate"), dict) else {}
    return {
        "id": event.get("id"),
        "aggregate": {
            key: aggregate.get(key)
            for key in ("title", "short_summary")
            if aggregate.get(key) is not None
        },
    }


def _bounded_error(value: object) -> str:
    return safe_external_text(value, limit=1000) or (
        "The selected provider did not return a valid response; "
        "Oída used its local structured fallback."
    )


def _context_value(value: bool | None, default: bool) -> bool:
    return default if value is None else value
