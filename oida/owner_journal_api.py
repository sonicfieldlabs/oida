"""Authenticated owner journal surfaces; not the application's public projection."""

from fastapi import APIRouter, HTTPException, Query

from oida.owner_journal import CursorMismatch


def owner_journal_router(journal):
    router = APIRouter()

    @router.get("/owner/journal")
    def events(
        after_sequence: int = Query(0, ge=0),
        producer_id: str | None = None,
        limit: int = Query(100, ge=1, le=500),
    ):
        try:
            return journal.events(
                after=after_sequence, producer_id=producer_id, limit=limit
            )
        except CursorMismatch as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/owner/snapshots")
    def snapshots(
        after_sequence: int = Query(0, ge=0),
        producer_id: str | None = None,
        limit: int = Query(100, ge=1, le=500),
        at_sequence: int | None = Query(None, ge=0),
    ):
        try:
            return journal.snapshots(
                after=after_sequence,
                producer_id=producer_id,
                limit=limit,
                at=at_sequence,
            )
        except CursorMismatch as exc:
            raise HTTPException(409, str(exc)) from exc

    def read_record(identifier):
        from akousma import AkousmataStore

        store = AkousmataStore()
        try:
            record = store.get(identifier)
        except KeyError:
            record = None
        finally:
            store.close()
        if record is None:
            raise HTTPException(404, "canonical record is unavailable")
        return record

    @router.get("/owner/records/{identifier}")
    def record(identifier: str):
        reference = journal.get("record_reference", identifier)
        if reference is None:
            raise HTTPException(404, "record is not referenced by this owner journal")
        value = read_record(identifier)
        from oida.claim_lifecycle import evaluate
        return dict(
            record=value,
            claim_evaluation=evaluate(value),
            reference=reference,
            current_sha256=journal.record_digest(value),
            producer_id=journal.producer_id,
            visibility="owner_only",
        )

    @router.post("/owner/records/{identifier}/reconcile")
    def reconcile(identifier: str):
        value = read_record(identifier)
        journal.record_reference(identifier, record=value)
        return dict(
            reference=journal.get("record_reference", identifier),
            producer_id=journal.producer_id,
        )

    return router
