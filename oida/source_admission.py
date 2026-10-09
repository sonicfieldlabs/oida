"""Caller-declared provenance for bounded audio admitted by existing owner routes."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from oida.contracts import AudioSegment


class SourceAdmission(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    adapter: Literal["file", "browser-microphone", "radio-window", "high-rate-device"]
    producer_id: str = Field(min_length=1, max_length=256)
    source_id: str = Field(min_length=1, max_length=256)
    source_time: str
    source_time_basis: Literal["caller-declared", "local-acquisition-start"] = "caller-declared"
    consent: Literal["granted", "denied", "unknown"]
    consent_ref: str = Field(min_length=1, max_length=512)
    raw_audio_policy: Literal["not_stored", "temp", "saved", "external_ref"]
    max_window_s: float = Field(gt=0, le=300)
    apparatus: dict[str, Any]

    @field_validator("source_time")
    @classmethod
    def aware_time(cls, value: str) -> str:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("source_time must include a timezone")
        return value

    @field_validator("apparatus")
    @classmethod
    def bounded_apparatus(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value, allow_nan=False).encode()) > 16384:
            raise ValueError("apparatus declaration exceeds 16 KiB")
        return value

    def check_policy(self, source_type: str, raw_audio_policy: str) -> None:
        expected = {"file": "file", "browser-microphone": "live_input",
                    "radio-window": "external_stream", "high-rate-device": "live_input"}
        if self.consent != "granted":
            raise ValueError("source admission requires declared granted consent")
        if source_type != expected[self.adapter]:
            raise ValueError("source adapter and source_type disagree")
        if self.raw_audio_policy != raw_audio_policy:
            raise ValueError("source admission and effective raw_audio_policy disagree")

    def receipt(self, segment: AudioSegment) -> dict[str, Any]:
        # Inspect the admitted file, never infer hardware bandwidth from its rate.
        duration = float(segment.metadata["dsp"]["durationSeconds"])
        if duration > self.max_window_s:
            raise ValueError("source exceeds the declared bounded window")
        return {
            "contract": "oida/source-admission/v1",
            **self.model_dump(),
            "basis": "caller declaration; not independent consent or apparatus verification",
            "subject_ref": "sha256:" + str(segment.data_ref.sha256),
            "sampled_representation": {
                "sample_rate_hz": segment.sample_rate,
                "channels": segment.channels,
                "duration_s": duration,
            },
        }
