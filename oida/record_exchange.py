"""Owner-mediated local record handoff with durable, content-bound receipts."""

import hashlib
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from akousma import AkousmataStore
from akousma.listening_contracts import assert_supported_contracts
from akousma.record_evolution import next_record_errors
from akousma.listening_context import listening_context_errors
from oida.ensemble_runtime import load
from oida.operation_control import checkpoint
from oida.owner_journal import canonical

CONTRACT = "oida/record-exchange/v1"
REQUIRED = [
    CONTRACT,
    "earworm/akousma/v1.7",
    "earworm/listening-context/v1",
    "akouo/agent-report/v0.1",
]


class OfferRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    record_id: str = Field(min_length=1, max_length=256)
    recipient_id: str = Field(min_length=1, max_length=256)
    supported_contracts: list[str] = Field(min_length=1, max_length=32)
    idempotency_key: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    permission_ref: str = Field(min_length=1, max_length=256)


class ExchangePacket(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    contract: Literal["oida/record-exchange/v1"]
    sender_id: str = Field(min_length=1, max_length=256)
    recipient_id: str = Field(min_length=1, max_length=256)
    idempotency_key: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    supported_contracts: list[str] = Field(min_length=1, max_length=32)
    required_contracts: list[str] = Field(min_length=1, max_length=32)
    permission_ref: str = Field(min_length=1, max_length=256)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    record: dict


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def exchange_router(operations, journal, preflight):
    router = APIRouter()

    def validate_permission(record, ref):
        if (
            not ref.strip()
            or record.get("provenance", {}).get("consent_status") == "restricted"
        ):
            raise ValueError(
                "A nonrestricted source and explicit transfer permission are required"
            )
        # A handoff contains the whole record, so a content-withholding covenant
        # cannot be satisfied by selectively redacting a derived prompt.
        from oida.reasoning.evidence import covenant_blocks_untyped_prose

        if covenant_blocks_untyped_prose(record.get("covenant")):
            raise ValueError("Retained covenant prevents whole-record transfer")

    @router.get("/owner/exchange/capabilities")
    def capabilities():
        return dict(
            contract=CONTRACT,
            recipient_id=journal.producer_id,
            supported_contracts=REQUIRED,
            required_contracts=REQUIRED,
            transport="owner_mediated",
            execution="record_transfer_only",
        )

    @router.post("/owner/exchange/offers")
    def offer(req: OfferRequest):
        preflight("file", None, None, False)
        store = AkousmataStore()
        try:
            assert_supported_contracts(REQUIRED, req.supported_contracts)
            record = load(store, req.record_id)
            validate_permission(record, req.permission_ref)
            if req.recipient_id == journal.producer_id:
                raise ValueError("Exchange requires a distinct recipient")
            return ExchangePacket(
                contract=CONTRACT,
                sender_id=journal.producer_id,
                recipient_id=req.recipient_id,
                idempotency_key=req.idempotency_key,
                supported_contracts=REQUIRED,
                required_contracts=REQUIRED,
                permission_ref=req.permission_ref,
                source_sha256=digest(record),
                record=record,
            )
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            store.close()

    @router.post("/owner/exchange/receive")
    def receive(packet: ExchangePacket):
        try:
            preflight("file", None, None, True)
            assert_supported_contracts(REQUIRED, packet.supported_contracts)
            assert_supported_contracts(REQUIRED, packet.required_contracts)
            assert_supported_contracts(packet.required_contracts, REQUIRED)
            if (
                packet.recipient_id != journal.producer_id
                or packet.sender_id == journal.producer_id
            ):
                raise ValueError("Exchange recipient does not match this owner")
            validate_permission(packet.record, packet.permission_ref)
            if len(canonical(packet.record).encode()) > 2 * 1024 * 1024:
                raise ValueError("Exchange source exceeds 2 MiB")
            errors = next_record_errors(packet.record)
            context = packet.record.get("extensions", {}).get(
                "earworm_listening_context"
            )
            if not errors and context is not None:
                errors.extend(listening_context_errors(context, packet.record))
            if errors:
                raise ValueError("; ".join(errors))
            if digest(packet.record) != packet.source_sha256:
                raise ValueError("Exchange source digest mismatch")
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(400, str(exc)) from exc

        fingerprint = digest(packet.model_dump())
        operation_id = "exchange_" + digest([packet.sender_id, packet.idempotency_key])
        identifier = packet.record["akousma_id"]
        receipt = dict(
            contract="oida/record-exchange-receipt/v1",
            operation_id=operation_id,
            sender_id=packet.sender_id,
            recipient_id=journal.producer_id,
            idempotency_key=packet.idempotency_key,
            source_record_ref=identifier,
            source_sha256=packet.source_sha256,
            akousma_id=identifier,
            permission_ref=packet.permission_ref,
            negotiated_contracts=REQUIRED,
            outcome="accepted",
            execution="record_transfer_only",
            new_pass=False,
        )

        def finish(store, *, recovered=False):
            if digest(store.get(identifier)) != packet.source_sha256:
                raise HTTPException(
                    409, "Canonical received record is missing or changed"
                )
            journal.record_reference(identifier, record=packet.record)
            journal.save(
                "record_exchange",
                operation_id,
                dict(fingerprint=fingerprint, phase="complete", receipt=receipt),
            )
            return dict(**receipt, replayed=recovered)

        # Serialize lazy store initialization, and refuse active retries before
        # opening another SQLite connection while its peer is committing.
        with operations.lock:
            if operation_id in operations.active:
                raise HTTPException(
                    409, "Exchange is active; inspect its operation receipt"
                )
            store = AkousmataStore()
        try:
            with operations.lock:
                previous = journal.get("record_exchange", operation_id)
                operation = journal.get("operation", operation_id)
                if previous:
                    if previous["fingerprint"] != fingerprint:
                        raise HTTPException(
                            409,
                            "Idempotency key is bound to different content or permission",
                        )
                    if previous["phase"] == "complete":
                        if digest(store.get(identifier)) != packet.source_sha256:
                            raise HTTPException(
                                409,
                                "Canonical received record changed after acceptance",
                            )
                        # Repair a crash between the accepted receipt and operation completion.
                        if operation_id not in operations.active and (
                            not operation
                            or operation["status"] != "complete"
                            or operation.get("akousma_id") != identifier
                        ):
                            operations.save(
                                operation_id, "complete", akousma_id=identifier
                            )
                        return dict(**previous["receipt"], replayed=True)
                    if (
                        operation_id not in operations.active
                        and previous["phase"] == "committing"
                        and operation
                        and operation["status"] not in ("cancelled", "refused")
                        and store.get(identifier) is not None
                    ):
                        result = finish(store, recovered=True)
                        operations.save(operation_id, "complete", akousma_id=identifier)
                        return result
                if operation:
                    raise HTTPException(
                        409, "Exchange already reserved; inspect its operation receipt"
                    )

            def accept():
                existing = store.get(identifier)
                if existing is not None and canonical(existing) != canonical(
                    packet.record
                ):
                    raise HTTPException(
                        409,
                        "Source identity collides with a different canonical record",
                    )
                journal.save(
                    "record_exchange",
                    operation_id,
                    dict(fingerprint=fingerprint, phase="prepared"),
                )
                preflight("file", None, None, True)
                checkpoint(seal=True)
                journal.save(
                    "record_exchange",
                    operation_id,
                    dict(fingerprint=fingerprint, phase="committing"),
                )
                if existing is None:
                    store.put(packet.record)
                return finish(store)

            return operations.run(operation_id, accept)
        finally:
            store.close()

    return router
