"""Atomic routing reservations; unknown remote outcomes keep their full hold."""

import json
import hashlib
import math
import time
from datetime import datetime

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from oida.owner_journal import canonical
from oida.contracts import now_iso


class PriceBasis(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    provider_id: str = Field(min_length=1, max_length=120)
    model_id: str = Field(min_length=1, max_length=255)
    max_request_usd: float = Field(ge=0, le=100)
    usd_per_million_tokens: float | None = Field(default=None, ge=0)
    basis: str = Field(min_length=10, max_length=2000)
    revision: str = Field(min_length=1, max_length=120)
    expires_at: str
    # A planning guess is insufficient for a hard cap. This records the
    # operator's verified provider billing ceiling, including hidden retries.
    provider_enforced_bound: bool = False

    @model_validator(mode="after")
    def dated(self):
        parsed = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("A price basis needs a timezone-aware expiry")
        return self


class CostSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    workspace_ceiling_usd: float | None = Field(default=None, ge=0)
    prices: list[PriceBasis] = Field(default_factory=list, max_length=100)
    expected_revision: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def unique(self):
        keys = [(p.provider_id, p.model_id) for p in self.prices]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate provider/model price basis")
        return self


class CostReconciliation(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    billed_usd: float = Field(ge=0)
    receipt_reference: str = Field(min_length=5, max_length=500)


class RoutingCosts:
    def __init__(self, journal):
        self.journal = journal
        with journal.connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS routing_costs (id TEXT PRIMARY KEY,scope TEXT NOT NULL,provider TEXT NOT NULL,model TEXT,held REAL NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS routing_cost_scope ON routing_costs(scope)")
            db.execute("CREATE TABLE IF NOT EXISTS routing_scope_budgets (scope TEXT PRIMARY KEY, ceiling REAL NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS routing_price_violations (provider TEXT,model TEXT,revision TEXT,PRIMARY KEY(provider,model,revision))")
            for identifier, raw in db.execute("SELECT id,payload FROM routing_costs WHERE status='reserved'").fetchall():
                receipt = json.loads(raw)
                receipt.update(status="unresolved", recovery="Owner restarted; reservation retained until provider reconciliation")
                db.execute("UPDATE routing_costs SET status='unresolved',payload=? WHERE id=?", (canonical(receipt), identifier))

    def config(self):
        return self.journal.get("routing_cost_config", "primary") or {"workspace_ceiling_usd": None, "prices": []}

    def configure(self, value):
        encoded = canonical(value.model_dump(mode="json", exclude={"expected_revision"}))
        with self.journal.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM snapshots WHERE kind='routing_cost_config' AND subject='primary'").fetchone()
            old = json.loads(row[0]) if row else {"workspace_ceiling_usd": None, "prices": []}
            if value.expected_revision and value.expected_revision != hashlib.sha256(canonical(old).encode()).hexdigest():
                raise HTTPException(409, "Routing cost settings changed; reload before saving")
            sequence = db.execute("INSERT INTO events(kind,subject,created_at,payload) VALUES ('routing_cost_config','primary',?,?)", (now_iso(), encoded)).lastrowid
            db.execute("INSERT INTO snapshots VALUES ('routing_cost_config','primary',?,?) ON CONFLICT(kind,subject) DO UPDATE SET sequence=excluded.sequence,payload=excluded.payload", (sequence, encoded))
        return self.state()

    def reserve(self, identifier, scope, provider, model, cap, *, deterministic=False):
        with self.journal.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            raw = db.execute("SELECT payload FROM snapshots WHERE kind='routing_cost_config' AND subject='primary'").fetchone()
            config = json.loads(raw[0]) if raw else {"prices": [], "workspace_ceiling_usd": None}
            prior = db.execute("SELECT ceiling FROM routing_scope_budgets WHERE scope=?", (scope,)).fetchone()
            if prior:
                cap = min(cap, prior[0]) if cap is not None else prior[0]
            if cap is not None:
                db.execute("INSERT INTO routing_scope_budgets VALUES (?,?) ON CONFLICT(scope) DO UPDATE SET ceiling=min(ceiling,excluded.ceiling)", (scope, cap))
            price = next((p for p in config["prices"] if p["provider_id"] == provider and p["model_id"] == model), None)
            qualified = price and price["provider_enforced_bound"] and datetime.fromisoformat(price["expires_at"].replace("Z", "+00:00")).timestamp() > time.time()
            if price and db.execute("SELECT 1 FROM routing_price_violations WHERE provider=? AND model=? AND revision=?", (provider, model, price["revision"])).fetchone():
                qualified = False
            capped = cap is not None or config.get("workspace_ceiling_usd") is not None
            if capped and not deterministic and not qualified:
                raise HTTPException(409, "A hard monetary cap requires a current provider-enforced cost basis")
            amount = 0.0 if deterministic or not qualified else price["max_request_usd"]
            receipt = {"id": identifier, "scope": scope, "provider_id": provider, "model_id": model, "price": price if qualified else None, "reserved_usd": amount if deterministic or qualified else None, "status": "reserved" if deterministic or qualified else "unpriced", "created_at": time.time()}
            if db.execute("SELECT 1 FROM routing_costs WHERE id=?", (identifier,)).fetchone():
                raise HTTPException(409, "This provider attempt already has a cost reservation")
            for where, params, ceiling in (("scope=?", (scope,), cap), ("1=1", (), config.get("workspace_ceiling_usd"))):
                if ceiling is None:
                    continue
                spent, unpriced = db.execute(f"SELECT coalesce(sum(held),0),coalesce(sum(status='unpriced'),0) FROM routing_costs WHERE {where}", params).fetchone()
                if unpriced or spent + amount > ceiling + 1e-9:
                    raise HTTPException(409, "Shared routing cost ceiling is exhausted or contains unresolved unpriced work")
            db.execute("INSERT INTO routing_costs VALUES (?,?,?,?,?,?,?)", (identifier, scope, provider, model, amount, receipt["status"], canonical(receipt)))
        return receipt

    def reconcile(self, identifier, value):
        with self.journal.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM routing_costs WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise HTTPException(404, "Cost reservation not found")
            receipt = json.loads(row[0])
            has_queue = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='routing_jobs'").fetchone()
            active = has_queue and db.execute("SELECT 1 FROM routing_jobs WHERE id=? AND status IN ('queued','running')", (identifier.rsplit(":", 1)[0],)).fetchone()
            if receipt["status"] == "reserved" or active:
                raise HTTPException(409, "Wait until the active provider attempt settles")
            receipt.setdefault("reconciliations", []).append({**value.model_dump(), "at": time.time()})
            price = receipt.get("price")
            if price and value.billed_usd > price["max_request_usd"]:
                receipt["exceeds_basis"] = True
                db.execute("INSERT OR IGNORE INTO routing_price_violations VALUES (?,?,?)", (receipt["provider_id"], receipt["model_id"], price["revision"]))
            receipt.update(status="reconciled", committed_usd=value.billed_usd)
            db.execute("UPDATE routing_costs SET held=?,status='reconciled',payload=? WHERE id=?", (value.billed_usd, canonical(receipt), identifier))
        return receipt

    def settle(self, identifier, proposal=None):
        with self.journal.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload,held FROM routing_costs WHERE id=?", (identifier,)).fetchone()
            receipt, held = json.loads(row[0]), row[1]
            tokens = proposal.usage_tokens if proposal else None
            price = receipt.get("price") or {}
            rate = price.get("usd_per_million_tokens")
            calculated = tokens * rate / 1_000_000 if tokens is not None and rate is not None else None
            known_zero = receipt["status"] == "reserved" and receipt["reserved_usd"] == 0 and not price
            status = "settled" if known_zero or calculated is not None else "unpriced" if receipt["reserved_usd"] is None else "unresolved"
            if calculated is not None and (not math.isfinite(calculated) or calculated < 0):
                raise ValueError("Invalid provider cost")
            # Retain the planning ceiling even with reported usage: usage is
            # not an invoice and cancellation does not imply zero charges.
            held = max(held, calculated or 0)
            receipt.update(status=status, usage_tokens=tokens, calculated_usd=calculated, committed_usd=held if receipt["reserved_usd"] is not None else None, settled_at=time.time(), exceeds_basis=bool(calculated is not None and calculated > (receipt["reserved_usd"] or 0)))
            if receipt["exceeds_basis"]:
                db.execute("INSERT OR IGNORE INTO routing_price_violations VALUES (?,?,?)", (receipt["provider_id"], receipt["model_id"], price["revision"]))
            db.execute("UPDATE routing_costs SET held=?,status=?,payload=? WHERE id=?", (held, status, canonical(receipt), identifier))
        return receipt

    def state(self):
        with self.journal.connection() as db:
            held, unknown = db.execute("SELECT coalesce(sum(held),0),coalesce(sum(status='unpriced'),0) FROM routing_costs").fetchone()
            rows = db.execute("SELECT payload FROM routing_costs ORDER BY rowid DESC LIMIT 100").fetchall()
        config = self.config()
        return {"config": config, "revision": hashlib.sha256(canonical(config).encode()).hexdigest(), "committed_usd": held, "unpriced_attempts": unknown, "attempts": [json.loads(row[0]) for row in rows]}
