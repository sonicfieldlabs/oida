"""O18 delegates to the Akousmata-owned proposal/change service in the shared store."""

from fastapi import APIRouter, HTTPException


def research_router():
    router = APIRouter(prefix="/owner/research", tags=["research"])

    def call(operation, *args):
        try:
            from akousmata_app.paths import open_store
            from akousmata_app.research import proposals
        except ImportError as exc:
            raise HTTPException(
                503, "Compatible Akousmata research service is required"
            ) from exc
        store = open_store()
        try:
            return getattr(proposals, operation)(store, *args)
        except ImportError as exc:
            raise HTTPException(
                503, "Compatible AKOUO record workflow package is required"
            ) from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            store.close()

    @router.post("/proposals")
    def submit(body: dict):
        return call("submit", body)

    @router.post("/requests/{request_id}/cancel")
    def cancel(request_id: str):
        return call("cancel", request_id)

    @router.post("/changes/{record_id}")
    def changed(record_id: str):
        return call("changed", record_id)

    @router.get("/reconcile")
    def reconcile(limit: int = 32, after: str = ""):
        return call("reconcile", limit, after)

    @router.post("/changes/{record_id}/acknowledge")
    def acknowledge(record_id: str, body: dict):
        return call("acknowledge", record_id, body.get("sha256"))

    return router
