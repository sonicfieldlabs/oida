"""Durable trial-budget admission for external audio dispatch.

The ledger is the M2 execution/billing boundary. It holds a worst-case
reservation before any external request is dispatched, reconciles reported
usage afterwards, and retains a reservation whenever usage stays unknown
(a timeout after submission can still be billed). Denial happens before any
outbound request, so a refused run costs nothing.

Pricing rows record provider, region, effective date and basis. A model
without a recorded price basis is refused rather than estimated with invented
numbers. The ledger never claims to predict a provider's final invoice.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date
from contextlib import contextmanager
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from oida.storage import write_json_atomic


TRIAL_BUDGET_CONTRACT = "oida/trial-budget/v1"


class BudgetDenied(Exception):
    """Raised before dispatch; no outbound request may follow."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class PricingRow(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    provider_id: str
    model_id: str
    audio_tokens_per_second: float = Field(gt=0)
    audio_input_usd_per_mtok: float = Field(gt=0)
    output_usd_per_mtok: float = Field(gt=0)
    region: str = "unspecified"
    basis: str
    effective: str
    expires: str | None = None


# Documented 2026-09-12 pricing used for the first trial candidates
# (integration plan §6). Rows are worst-case planning bases, not quotations.
PRICING: tuple[PricingRow, ...] = (
    PricingRow(
        provider_id="google",
        model_id="gemini-3.5-flash-lite",
        audio_tokens_per_second=32.0,
        audio_input_usd_per_mtok=0.30,
        output_usd_per_mtok=2.50,
        region="global",
        basis="Google AI Studio documented audio tokenization and pricing",
        effective="2026-09-12",
    ),
    PricingRow(
        provider_id="google",
        model_id="gemini-3.8-flash",
        audio_tokens_per_second=32.0,
        audio_input_usd_per_mtok=0.75,
        output_usd_per_mtok=3.75,
        region="global",
        basis="Google AI Studio documented audio tokenization and pricing (promotional through 2026-12-31)",
        effective="2026-09-12",
        expires="2026-12-31",
    ),
    PricingRow(
        provider_id="google",
        model_id="gemini-3.1-pro-preview",
        audio_tokens_per_second=32.0,
        audio_input_usd_per_mtok=2.00,
        output_usd_per_mtok=12.00,
        region="global",
        basis="Google AI Studio documented audio tokenization and Pro Preview pricing (preview designation)",
        effective="2026-09-12",
    ),
    PricingRow(
        provider_id="alibaba",
        model_id="qwen3.5-omni-flash",
        audio_tokens_per_second=7.0,
        audio_input_usd_per_mtok=3.0,
        output_usd_per_mtok=2.20,
        region="ap-southeast-1",
        basis="Alibaba Model Studio Omni guide (seven audio-input tokens/second) and Model Studio pricing",
        effective="2026-09-12",
    ),
    PricingRow(
        provider_id="alibaba",
        model_id="qwen3.5-omni-plus",
        audio_tokens_per_second=7.0,
        audio_input_usd_per_mtok=11.0,
        output_usd_per_mtok=8.30,
        region="ap-southeast-1",
        basis="Alibaba Model Studio Omni guide (seven audio-input tokens/second) and Model Studio pricing",
        effective="2026-09-12",
    ),
)


class BudgetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    contract: str = TRIAL_BUDGET_CONTRACT
    enabled: bool = False
    total_ceiling_usd: float = Field(default=5.0, ge=0, le=5.0)
    per_provider_cap_usd: dict[str, float] = Field(default_factory=dict)
    max_calls: int = Field(default=64, ge=1, le=10_000)
    max_output_tokens_per_call: int = Field(default=4096, ge=16, le=1_000_000)


def quote_cost(
    provider_id: str,
    model_id: str,
    *,
    audio_seconds: float,
    max_output_tokens: int,
    text_input_tokens: int = 0,
    pricing: tuple[PricingRow, ...] = PRICING,
) -> dict[str, Any] | None:
    """Worst-case planning cost for one call, or None without a price basis."""
    normalized = str(model_id or "").strip().lower()
    row = next(
        (
            item
            for item in pricing
            if item.provider_id == provider_id
            and (
                item.model_id.lower() in {normalized, normalized.rsplit("/", 1)[-1]}
                or normalized
                in {item.model_id.lower(), item.model_id.lower().rsplit("/", 1)[-1]}
            )
        ),
        None,
    )
    if row is None or (row.expires and date.today().isoformat() > row.expires):
        return None
    if not math.isfinite(audio_seconds) or not 0 < audio_seconds <= 60:
        raise BudgetDenied("Audio duration must be verified within 0..60 seconds")
    if (
        type(max_output_tokens) is not int
        or max_output_tokens <= 0
        or type(text_input_tokens) is not int
        or text_input_tokens < 0
    ):
        raise BudgetDenied("Invalid token bound")
    audio_tokens = max(0.0, float(audio_seconds)) * row.audio_tokens_per_second
    output_tokens = max(0, int(max_output_tokens))
    cost = (
        (audio_tokens + text_input_tokens) * row.audio_input_usd_per_mtok / 1_000_000
        + output_tokens * row.output_usd_per_mtok / 1_000_000
    )
    return dict(
        provider_id=row.provider_id,
        model_id=row.model_id,
        audio_tokens=int(audio_tokens + 0.999),
        output_tokens=output_tokens,
        worst_case_usd=math.ceil(cost * 1_000_000) / 1_000_000,
        region=row.region,
        basis=row.basis,
        effective=row.effective,
        expires=row.expires,
        text_input_tokens=text_input_tokens,
        input_rate=row.audio_input_usd_per_mtok,
        output_rate=row.output_usd_per_mtok,
        kind="conservative_planning_bound",
    )


class BudgetLedger:
    """SQLite-backed trial authority shared by every process using one directory."""

    def __init__(self, directory: Path, *, clock=time.time):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.config_path = self.directory / "budget.json"
        self.db_path = self.directory / "budget.sqlite3"
        self.clock = clock
        self.lock = threading.RLock()
        self._db_init()
        self._config = self._load_config()
        if not self.config_path.exists():
            try:
                write_json_atomic(
                    self.config_path, self._config.model_dump(mode="json")
                )
            except OSError:
                pass

    def _file_config(self) -> BudgetConfig:
        if not self.config_path.exists():
            return BudgetConfig()
        try:
            return BudgetConfig.model_validate(
                json.loads(self.config_path.read_text(encoding="utf-8"))
            )
        except (OSError, json.JSONDecodeError, ValidationError):
            # A corrupt configuration keeps the trial closed; it never opens spend.
            return BudgetConfig()

    def _load_config(self, db=None) -> BudgetConfig:
        if db is None:
            with self._db() as connection:
                return self._load_config(connection)
        row = db.execute("SELECT payload FROM configuration WHERE id=1").fetchone()
        if row is None:
            return BudgetConfig()
        try:
            return BudgetConfig.model_validate(json.loads(row[0]))
        except (json.JSONDecodeError, ValidationError, TypeError):
            return BudgetConfig()

    @staticmethod
    def _revision(config: BudgetConfig) -> str:
        return hashlib.sha256(
            json.dumps(
                config.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()

    def _db_init(self) -> None:
        with self._db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS configuration ("
                "id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            db.execute(
                "INSERT OR IGNORE INTO metadata VALUES ('trial_id', ?)",
                ("trial_" + uuid4().hex,),
            )
            if db.execute("SELECT 1 FROM configuration WHERE id=1").fetchone() is None:
                initial = self._file_config()
                db.execute(
                    "INSERT INTO configuration VALUES (1, ?)",
                    (json.dumps(initial.model_dump(mode="json")),),
                )
            db.execute(
                "CREATE TABLE IF NOT EXISTS reservations ("
                "id TEXT PRIMARY KEY, created REAL, provider_id TEXT, model_id TEXT,"
                "status TEXT, reserved_usd REAL, settled_usd REAL, payload TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS probes ("
                "provider_id TEXT, model_id TEXT, tested_at REAL, status TEXT,"
                "fingerprint TEXT,"
                "PRIMARY KEY(provider_id, model_id))"
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(probes)")}
            if "fingerprint" not in columns:
                db.execute("ALTER TABLE probes ADD COLUMN fingerprint TEXT")

    @property
    def trial_id(self) -> str:
        with self._db() as db:
            row = db.execute(
                "SELECT value FROM metadata WHERE key='trial_id'"
            ).fetchone()
            return str(row[0])

    @contextmanager
    def _db(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def configure(self, updates: dict[str, Any]) -> BudgetConfig:
        with self.lock:
            with self._db() as db:
                current = self._load_config(db).model_dump()
                current.update(
                    {k: v for k, v in updates.items() if k in BudgetConfig.model_fields}
                )
                candidate = BudgetConfig.model_validate(current)
                if any(
                    not math.isfinite(v) or v < 0 or v > 5
                    for v in candidate.per_provider_cap_usd.values()
                ):
                    raise ValueError(
                        "Provider caps must be finite amounts within the trial ceiling"
                    )
                db.execute(
                    "UPDATE configuration SET payload=? WHERE id=1",
                    (json.dumps(candidate.model_dump(mode="json")),),
                )
            try:
                write_json_atomic(self.config_path, candidate.model_dump(mode="json"))
            except OSError:
                # SQLite is canonical. A failed human-readable mirror must not
                # make the committed configuration appear to have been refused.
                pass
            self._config = candidate
            return candidate

    update_config = configure

    @property
    def config(self) -> BudgetConfig:
        with self.lock:
            return self._load_config()

    def quote(
        self,
        provider_id: str,
        model_id: str,
        *,
        audio_seconds: float,
        max_output_tokens: int,
        text_input_tokens: int = 0,
    ) -> dict[str, Any] | None:
        return quote_cost(
            provider_id,
            model_id,
            audio_seconds=audio_seconds,
            max_output_tokens=max_output_tokens,
            text_input_tokens=text_input_tokens,
        )

    def _spent(self, db) -> tuple[float, dict[str, float], int]:
        total = 0.0
        per_provider: dict[str, float] = {}
        calls = 0
        for status, provider, reserved, settled, payload in db.execute(
            "SELECT status,provider_id,reserved_usd,settled_usd,payload FROM reservations"
        ):
            value = json.loads(payload)
            # Unresolved (unknown usage) retains its full reservation; it is
            # never counted as zero and never released by a restart.
            amount = (
                settled
                if status == "settled" and settled is not None
                else reserved
                if status in {"reserved", "unresolved"}
                else 0.0
            )
            total += amount
            per_provider[provider] = per_provider.get(provider, 0.0) + amount
            if status in {"reserved", "unresolved", "settled"} and value.get(
                "counts_as_call", True
            ):
                calls += 1
        return total, per_provider, calls

    def state(self) -> dict[str, Any]:
        with self.lock:
            with self._db() as db:
                config = self._load_config(db)
                total, per_provider, calls = self._spent(db)
                reservations = [
                    dict(
                        json.loads(payload),
                        status=status,
                        reserved_usd=reserved,
                        settled_usd=settled,
                    )
                    for status, reserved, settled, payload in db.execute(
                        "SELECT status,reserved_usd,settled_usd,payload FROM reservations "
                        "ORDER BY created DESC LIMIT 200"
                    )
                ]
                probes = [
                    dict(
                        provider_id=p, model_id=m, tested_at=t, status=s, fingerprint=f
                    )
                    for p, m, t, s, f in db.execute(
                        "SELECT provider_id,model_id,tested_at,status,fingerprint "
                        "FROM probes ORDER BY tested_at DESC"
                    )
                ]
            remaining = max(0.0, config.total_ceiling_usd - total)
            return dict(
                contract=TRIAL_BUDGET_CONTRACT,
                enabled=config.enabled,
                total_ceiling_usd=config.total_ceiling_usd,
                committed_usd=round(total, 6),
                remaining_usd=round(remaining, 6),
                per_provider_committed_usd={
                    key: round(value, 6) for key, value in per_provider.items()
                },
                calls_used=calls,
                max_calls=config.max_calls,
                reservations=reservations,
                probes=probes,
                policy=(
                    "Reservations are held before dispatch; unknown usage retains "
                    "its reservation until reconciled; estimates are planning "
                    "figures, never a provider invoice."
                ),
            )

    def reserve(
        self,
        provider_id: str,
        model_id: str,
        *,
        audio_seconds: float,
        max_output_tokens: int | None = None,
        reference: str | None = None,
        text_input_tokens: int = 0,
    ) -> dict[str, Any]:
        """Admit one external call or refuse before anything is sent."""
        with self.lock:
            with self._db() as db:
                config = self._load_config(db)
                if not config.enabled:
                    raise BudgetDenied(
                        "Cloud trial budget is not enabled; external audio dispatch stays closed"
                    )
                capped_output = min(
                    int(max_output_tokens or config.max_output_tokens_per_call),
                    config.max_output_tokens_per_call,
                )
                estimate = self.quote(
                    provider_id,
                    model_id,
                    audio_seconds=audio_seconds,
                    max_output_tokens=capped_output,
                    text_input_tokens=text_input_tokens,
                )
                if estimate is None:
                    raise BudgetDenied(
                        f"No recorded price basis for {provider_id}/{model_id}; "
                        "configure pricing before dispatch"
                    )
                total, per_provider, calls = self._spent(db)
                if calls + 1 > config.max_calls:
                    raise BudgetDenied("Trial call budget is exhausted")
                if total + estimate["worst_case_usd"] > config.total_ceiling_usd + 1e-9:
                    raise BudgetDenied(
                        f"Trial budget remaining (${max(0.0, config.total_ceiling_usd - total):.4f}) "
                        f"is below this request's worst-case estimate (${estimate['worst_case_usd']:.4f})"
                    )
                provider_cap = config.per_provider_cap_usd.get(provider_id)
                if provider_cap is not None and (
                    per_provider.get(provider_id, 0.0) + estimate["worst_case_usd"]
                    > provider_cap + 1e-9
                ):
                    raise BudgetDenied(
                        f"Provider spending cap for {provider_id} would be exceeded"
                    )
                reservation_id = "res-" + uuid4().hex
                payload = dict(
                    id=reservation_id,
                    provider_id=provider_id,
                    model_id=model_id,
                    reserved_usd=estimate["worst_case_usd"],
                    estimate=estimate,
                    max_output_tokens=capped_output,
                    text_input_tokens=text_input_tokens,
                    reference=reference,
                    counts_as_call=True,
                    configuration_revision=self._revision(config),
                )
                db.execute(
                    "INSERT INTO reservations VALUES (?,?,?,?,?,?,?,?)",
                    (
                        reservation_id,
                        self.clock(),
                        provider_id,
                        model_id,
                        "reserved",
                        estimate["worst_case_usd"],
                        None,
                        json.dumps(payload),
                    ),
                )
            return dict(payload, status="reserved")

    def _update(
        self,
        reservation_id: str,
        status: str,
        settled: float | None,
        extra: dict[str, Any],
    ) -> None:
        with self.lock:
            with self._db() as db:
                row = db.execute(
                    "SELECT payload,status FROM reservations WHERE id=?",
                    (reservation_id,),
                ).fetchone()
                if row is None:
                    raise ValueError("Unknown reservation")
                if row[1] != "reserved":
                    raise ValueError(
                        "Reservation already finalized; explicit reconciliation required"
                    )
                payload = dict(json.loads(row[0]))
                payload.update(extra)
                db.execute(
                    "UPDATE reservations SET status=?,settled_usd=?,payload=? WHERE id=?",
                    (status, settled, json.dumps(payload), reservation_id),
                )

    def release(self, reservation_id: str, *, reason: str) -> None:
        """Pre-submission failure: nothing was sent, so nothing is retained."""
        self._update(reservation_id, "released", None, dict(released_reason=reason))

    def assert_dispatch(self, reservation_id: str) -> None:
        """Recheck the global switch and configuration revision immediately before send."""
        with self.lock:
            with self._db() as db:
                row = db.execute(
                    "SELECT status,payload FROM reservations WHERE id=?",
                    (reservation_id,),
                ).fetchone()
                if row is None or row[0] != "reserved":
                    raise BudgetDenied("Trial reservation is no longer dispatchable")
                payload = json.loads(row[1])
                config = self._load_config(db)
                if not config.enabled:
                    raise BudgetDenied(
                        "Cloud trial budget was disabled before dispatch"
                    )
                if payload.get("configuration_revision") != self._revision(config):
                    raise BudgetDenied(
                        "Cloud trial configuration changed before dispatch"
                    )

    def settle(
        self,
        reservation_id: str,
        *,
        usage: dict[str, Any] | None,
        reported_cost_usd: float | None = None,
        outcome: str,
    ) -> dict[str, Any]:
        """Reconcile a completed or failed dispatch.

        Unknown usage keeps the reservation held as ``unresolved``; it is never
        coerced to zero. A provider-reported cost, when present, is recorded
        alongside the local estimate.
        """
        if outcome not in {"complete", "failed", "unknown_remote_outcome"}:
            raise ValueError("Unknown outcome")
        with self.lock:
            with self._db() as db:
                row = db.execute(
                    "SELECT payload,reserved_usd FROM reservations WHERE id=?",
                    (reservation_id,),
                ).fetchone()
                if row is None:
                    raise ValueError("Unknown reservation")
                payload = json.loads(row[0])
                reserved = row[1]
            input_tokens = (
                usage.get("input_tokens") if isinstance(usage, dict) else None
            )
            output_tokens = (
                usage.get("output_tokens") if isinstance(usage, dict) else None
            )
            known = (
                type(input_tokens) is int
                and input_tokens >= 0
                and type(output_tokens) is int
                and output_tokens >= 0
            )
            if outcome == "unknown_remote_outcome" or not known:
                extra = dict(
                    usage=usage if isinstance(usage, dict) else None,
                    reported_cost_usd=reported_cost_usd,
                    outcome=outcome,
                    usage_status="unknown" if not known else "partial",
                )
                self._update(reservation_id, "unresolved", None, extra)
                return dict(payload, status="unresolved", **extra)
            # Use the reservation's pinned rates, never a later pricing table.
            rates = payload.get("estimate", {})
            reasoning_tokens = (usage or {}).get("reasoning_tokens")
            if payload["provider_id"] == "google":
                if type(reasoning_tokens) is not int or reasoning_tokens < 0:
                    self._update(
                        reservation_id,
                        "unresolved",
                        None,
                        dict(usage=usage, outcome=outcome, usage_status="partial"),
                    )
                    return dict(payload, status="unresolved")
                output_tokens += reasoning_tokens
            calculated = (
                input_tokens * rates.get("input_rate", 0)
                + output_tokens * rates.get("output_rate", 0)
            ) / 1_000_000
            # Keep the bound held during the trial; estimated token costs are not invoices.
            settled = max(
                reserved,
                calculated,
                reported_cost_usd
                if isinstance(reported_cost_usd, (int, float))
                and math.isfinite(reported_cost_usd)
                and reported_cost_usd >= 0
                else 0,
            )
            extra = dict(
                usage=usage if isinstance(usage, dict) else None,
                reported_cost_usd=reported_cost_usd,
                outcome=outcome,
                usage_status="reported",
            )
            self._update(reservation_id, "settled", round(settled, 6), extra)
            return dict(
                payload, status="settled", settled_usd=round(settled, 6), **extra
            )

    def record_probe(
        self,
        provider_id: str,
        model_id: str,
        *,
        fingerprint: str = "legacy_unqualified",
        status: str = "inference_tested",
    ) -> None:
        """Durable record of an authorized audio probe establishing account readiness."""
        with self.lock:
            with self._db() as db:
                db.execute(
                    "INSERT OR REPLACE INTO probes "
                    "(provider_id,model_id,tested_at,status,fingerprint) VALUES (?,?,?,?,?)",
                    (provider_id, model_id, self.clock(), status, fingerprint),
                )

    def is_probed(
        self,
        provider_id: str,
        model_id: str,
        *,
        fingerprint: str | None = None,
    ) -> bool:
        """Check whether provider and model have established account readiness via probe."""
        with self.lock:
            with self._db() as db:
                row = db.execute(
                    "SELECT status,fingerprint FROM probes WHERE provider_id=? AND model_id=?",
                    (provider_id, model_id),
                ).fetchone()
                return bool(
                    row
                    and row[0] == "inference_tested"
                    and (fingerprint is None or row[1] == fingerprint)
                )

    def invalidate_probes(self, provider_id: str) -> None:
        with self.lock:
            with self._db() as db:
                db.execute("DELETE FROM probes WHERE provider_id=?", (provider_id,))


def reservation_reference(value: str | None) -> str | None:
    if value is None:
        return None
    return re.sub(r"[^A-Za-z0-9_.:-]", "_", str(value))[:200]
