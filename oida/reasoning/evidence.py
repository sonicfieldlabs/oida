from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterable
from typing import Any

from oida.reasoning.contracts import EvidenceItem, EvidencePacket, EvidencePermissions


CLAIM_CATEGORIES = ("heard", "measured", "inferred", "interpreted", "speculative", "undetermined")
SAFE_FEATURES = (
    "duration_s",
    "sample_rate",
    "channels",
    "peakDbfs",
    "rmsDbfs",
    "crestFactorDb",
    "integratedLufs",
    "loudnessRangeLu",
    "zeroCrossingRate",
    "spectralCentroidHz",
    "spectralRolloffHz",
    "spectralFlatness",
    "onsetDensityPerSec",
    "bpmCandidate",
    "interChannelCorrelation",
    "stereoWidth",
    "channelBalanceDb",
    "silenceRatio",
    "clippedSampleRatio",
)


class EvidencePacketBuilder:
    """Build a bounded, path-free evidence packet from listening events.

    This is a whitelist transformation, not a generic redactor. Source URIs,
    segment data refs, artifacts, raw reports, user notes, and arbitrary nested
    event fields are never traversed into a packet.
    """

    def build(
        self,
        *,
        event: dict[str, Any],
        question: str,
        comparison_events: Iterable[dict[str, Any]] | None = None,
        memory_context: Iterable[dict[str, Any]] | None = None,
        include_transcript: bool = False,
        include_memory_content: bool = False,
    ) -> EvidencePacket:
        question = _bounded_text(question, limit=16_000) or ""
        if not question:
            raise ValueError("evidence packet requires a question")
        primary_id = _event_id(event)
        comparisons = list(comparison_events or [])
        if len(comparisons) > 3:
            raise ValueError("at most three comparison events may be included")
        comparison_ids = [_event_id(item) for item in comparisons]
        if primary_id in comparison_ids or len(comparison_ids) != len(set(comparison_ids)):
            raise ValueError("comparison events must be distinct from the primary event and each other")

        items: list[EvidenceItem] = []
        refs: set[str] = set()
        transcript_included = False
        for candidate in [event, *comparisons]:
            event_id = _event_id(candidate)
            self._append(
                EvidenceItem(
                    ref=f"event:{_ref_token(event_id)}:anchor",
                    kind="event_anchor",
                    value={"event_id": event_id},
                    event_id=event_id,
                    source="oida",
                ),
                items,
                refs,
            )
            added_transcript = self._append_event(
                candidate,
                items,
                refs,
                include_transcript=include_transcript,
            )
            transcript_included = transcript_included or added_transcript

        memory_included = self._append_memory(
            memory_context or [],
            items,
            refs,
            include_content=(
                include_memory_content
                and not _covenant_withholds(event, "memory")
                and not covenant_blocks_untyped_prose(event.get("covenant"))
            ),
        )
        covenant = _safe_covenant(event.get("covenant"))
        return EvidencePacket(
            primary_event_id=primary_id,
            comparison_event_ids=comparison_ids,
            question=question,
            items=items,
            permissions=EvidencePermissions(
                transcript_included=transcript_included,
                memory_content_included=memory_included,
                external_safe=True,
            ),
            covenant=covenant,
        )

    def _append_event(
        self,
        event: dict[str, Any],
        items: list[EvidenceItem],
        refs: set[str],
        *,
        include_transcript: bool,
    ) -> bool:
        event_id = _event_id(event)
        ref_event_id = _ref_token(event_id)
        aggregate = event.get("aggregate") if isinstance(event.get("aggregate"), dict) else {}
        claims = _claim_summary(event)
        event_has_transcript = _event_has_transcript(claims)
        output_subjects = _covenant_output_subjects(event)
        # Aggregate, route, warning, and legacy claim prose has no field-level
        # speech provenance. A v0.1 event can contain verbatim words without a
        # claim_summary marker, so untyped prose requires the transcript opt-in
        # even when no explicit transcript claim was detected.
        sensitive_summary = (
            not include_transcript
            or bool(output_subjects)
        )
        if not sensitive_summary:
            for key in ("title", "short_summary", "detailed_summary"):
                value = _bounded_text(aggregate.get(key))
                if value:
                    self._append(
                        EvidenceItem(
                            ref=f"event:{ref_event_id}:summary:{key}",
                            kind="summary",
                            value=value,
                            event_id=event_id,
                        ),
                        items,
                        refs,
                    )

        transcript_added = False
        if claims:
            for category in CLAIM_CATEGORIES:
                raw_claims = claims.get(category) if isinstance(claims.get(category), list) else []
                for index, raw in enumerate(raw_claims):
                    claim = raw if isinstance(raw, dict) else {"statement": raw}
                    statement = _bounded_text(claim.get("statement"))
                    if not statement:
                        continue
                    transcript = _is_transcript_claim(claim)
                    source = str(claim.get("source") or "").strip().lower()
                    if (
                        event_has_transcript
                        and not include_transcript
                        and not (category == "measured" and source in {"dsp", "metadata", "human"})
                    ):
                        continue
                    if transcript and (
                        not include_transcript
                        or _covenant_withholds(event, "transcript")
                        or _covenant_withholds(event, "speaker-identity")
                    ):
                        continue
                    if output_subjects or _claim_withheld(event, claim, category):
                        continue
                    kind = "transcript" if transcript else "claim"
                    self._append(
                        EvidenceItem(
                            ref=f"event:{ref_event_id}:claim:{category}:{index}",
                            kind=kind,
                            value=statement,
                            event_id=event_id,
                            category=category,
                            confidence=_bounded_text(claim.get("confidence"), limit=40),
                            basis=_bounded_text(claim.get("basis"), limit=2000),
                            source=_bounded_text(claim.get("source"), limit=80),
                            time_range=_time_range(claim.get("time_range")),
                        ),
                        items,
                        refs,
                    )
                    transcript_added = transcript_added or transcript
        else:
            if not _covenant_withholds(event, "speech"):
                self._append_aggregate_claims(
                    event_id,
                    ref_event_id,
                    aggregate,
                    items,
                    refs,
                    suppress_textual=sensitive_summary,
                )

        features = event.get("features") if isinstance(event.get("features"), dict) else {}
        spectral_withheld = _covenant_withholds(event, "spectral-detail")
        for key in SAFE_FEATURES:
            if spectral_withheld and key not in {"duration_s", "sample_rate", "channels"}:
                continue
            value = _finite_number(features.get(key))
            if value is None:
                continue
            self._append(
                EvidenceItem(
                    ref=f"event:{ref_event_id}:feature:{key}",
                    kind="feature",
                    value=value,
                    event_id=event_id,
                    category="measured",
                    source="dsp",
                ),
                items,
                refs,
            )
        if not spectral_withheld:
            band_energy = features.get("bandEnergy") if isinstance(features.get("bandEnergy"), dict) else {}
            for band in sorted(band_energy):
                value = _finite_number(band_energy.get(band))
                if value is None:
                    continue
                token = _ref_token(str(band))
                self._append(
                    EvidenceItem(
                        ref=f"event:{ref_event_id}:feature:bandEnergy.{token}",
                        kind="feature",
                        value=value,
                        event_id=event_id,
                        category="measured",
                        source="dsp",
                    ),
                    items,
                    refs,
                )

        if not sensitive_summary:
            routes = event.get("routes") if isinstance(event.get("routes"), list) else []
            for index, route in enumerate(routes):
                if not isinstance(route, dict):
                    continue
                summary = _bounded_text(route.get("summary"))
                if not summary:
                    continue
                route_id = _ref_token(str(route.get("route_id") or "route"))
                self._append(
                    EvidenceItem(
                        ref=f"event:{ref_event_id}:route:{route_id}:{index}",
                        kind="route",
                        value=summary,
                        event_id=event_id,
                    ),
                    items,
                    refs,
                )

        warnings = (
            aggregate.get("warnings")
            if isinstance(aggregate.get("warnings"), list) and not sensitive_summary
            else []
        )
        for index, warning in enumerate(warnings):
            value = _bounded_text(warning)
            if value:
                self._append(
                    EvidenceItem(
                        ref=f"event:{ref_event_id}:uncertainty:{index}",
                        kind="uncertainty",
                        value=value,
                        event_id=event_id,
                        category="undetermined",
                    ),
                    items,
                    refs,
                )
        return transcript_added

    def _append_aggregate_claims(
        self,
        event_id: str,
        ref_event_id: str,
        aggregate: dict[str, Any],
        items: list[EvidenceItem],
        refs: set[str],
        *,
        suppress_textual: bool,
    ) -> None:
        facts = aggregate.get("signal_facts") if isinstance(aggregate.get("signal_facts"), list) else []
        for index, fact in enumerate(facts):
            value = _bounded_text(fact)
            if value and not suppress_textual:
                self._append(
                    EvidenceItem(
                        ref=f"event:{ref_event_id}:claim:measured:{index}",
                        kind="claim",
                        value=value,
                        event_id=event_id,
                        category="measured",
                    ),
                    items,
                    refs,
                )
        hypotheses = aggregate.get("hypotheses") if isinstance(aggregate.get("hypotheses"), list) else []
        for index, raw in enumerate(hypotheses):
            hypothesis = raw if isinstance(raw, dict) else {"statement": raw}
            value = _bounded_text(hypothesis.get("statement"))
            if value and not suppress_textual:
                self._append(
                    EvidenceItem(
                        ref=f"event:{ref_event_id}:claim:inferred:{index}",
                        kind="claim",
                        value=value,
                        event_id=event_id,
                        category="inferred",
                        confidence=_bounded_text(hypothesis.get("confidence"), limit=40),
                        basis=_bounded_text(hypothesis.get("basis"), limit=2000),
                    ),
                    items,
                    refs,
                )

    def _append_memory(
        self,
        values: Iterable[dict[str, Any]],
        items: list[EvidenceItem],
        refs: set[str],
        *,
        include_content: bool,
    ) -> bool:
        if not include_content:
            return False
        added = False
        for raw in list(values)[:25]:
            if not isinstance(raw, dict):
                continue
            trace = raw.get("trace") if isinstance(raw.get("trace"), dict) else raw
            if not _memory_covenant_allows_content(trace):
                continue
            trace_id = _bounded_text(trace.get("id") or raw.get("trace_id"), limit=160)
            if not trace_id:
                continue
            value = {
                "title": _bounded_text(trace.get("title")),
                "summary": _bounded_text(trace.get("summary")),
                "score": _finite_number(raw.get("score")),
                "basis": _bounded_text(raw.get("basis"), limit=500),
            }
            value = {key: item for key, item in value.items() if item is not None}
            self._append(
                EvidenceItem(
                    ref=f"memory:{_ref_token(trace_id)}",
                    kind="memory",
                    value=value,
                    source="memory",
                ),
                items,
                refs,
            )
            added = True
        return added

    @staticmethod
    def _append(item: EvidenceItem, items: list[EvidenceItem], refs: set[str]) -> None:
        if item.ref in refs:
            return
        refs.add(item.ref)
        items.append(item)


def safe_external_text(value: Any, *, limit: int = 12_000) -> str | None:
    """Return bounded, locator-free text suitable for an evidence packet."""

    return _bounded_text(value, limit=limit)


def covenant_blocks_untyped_prose(value: Any) -> bool:
    """Return whether free-form history cannot cross this policy boundary.

    Memory previews and prior dialogue lack field-level provenance. If a
    covenant withholds any output subject, those prose blobs cannot prove the
    subject is absent and therefore remain local.
    """

    if not isinstance(value, dict):
        return False
    withheld = value.get("withheld") if isinstance(value.get("withheld"), list) else []
    if any(isinstance(item, dict) for item in withheld):
        return True
    applied = value.get("rules_applied") if isinstance(value.get("rules_applied"), list) else []
    return any(
        str(rule).startswith(("do_not_reveal:", "ignore:", "coarsen:", "do_not_retain:memory"))
        for rule in applied
    )


def _event_id(event: dict[str, Any]) -> str:
    if not isinstance(event, dict) or not str(event.get("id") or "").strip():
        raise ValueError("evidence packet requires listening events with ids")
    raw = str(event["id"]).strip()[:4096]
    sanitized = _bounded_text(raw, limit=255)
    if not sanitized:
        raise ValueError("evidence packet requires a shareable listening event id")
    if sanitized != raw:
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
        return f"event-redacted-{digest}"
    return sanitized


def _claim_summary(event: dict[str, Any]) -> dict[str, Any]:
    routes = event.get("routes") if isinstance(event.get("routes"), list) else []
    for route in routes:
        if not isinstance(route, dict):
            continue
        structured = route.get("structured") if isinstance(route.get("structured"), dict) else {}
        claims = structured.get("claim_summary")
        if isinstance(claims, dict):
            return claims
    return {}


def _is_transcript_claim(claim: dict[str, Any]) -> bool:
    source = str(claim.get("source") or "").strip().lower()
    statement = str(claim.get("statement") or "").strip().lower()
    basis = str(claim.get("basis") or "").strip().lower()
    if claim.get("speech_content") is True or source == "transcript":
        return True
    provenance = f"{source} {basis}"
    if any(
        marker in provenance
        for marker in (
            " asr",
            "automatic speech recognition",
            "speech recognition",
            "spoken words",
            "verbatim speech",
            "transcript",
        )
    ):
        return True
    if statement.startswith("transcript"):
        return True
    return bool(
        re.search(
            r"\b(?:person|speaker|someone|somebody|voice)\s+(?:says?|said|speaks?|spoke|utters?|uttered)\b",
            statement,
        )
    )


def _event_has_transcript(claims: dict[str, Any]) -> bool:
    for category in CLAIM_CATEGORIES:
        for claim in claims.get(category, []) if isinstance(claims.get(category), list) else []:
            if isinstance(claim, dict) and _is_transcript_claim(claim):
                return True
    return False


def _claim_withheld(event: dict[str, Any], claim: dict[str, Any], category: str) -> bool:
    statement = str(claim.get("statement") or "").lower()
    basis = str(claim.get("basis") or "").lower()
    source = str(claim.get("source") or "").lower()
    if _covenant_withholds(event, "speech") and not (
        category == "measured" and source in {"dsp", "metadata", "human"}
    ):
        return True
    if _covenant_withholds(event, "affect") and category == "interpreted":
        return True
    if _covenant_withholds(event, "events") and statement.startswith("sound event"):
        return True
    if _covenant_withholds(event, "speaker-identity") and any(
        term in f"{statement} {basis}" for term in ("speaker", "voice identity", "speech dimensions")
    ):
        return True
    if _covenant_withholds(event, "song-identity") and any(
        term in statement for term in ("song identity", "identified song", "artist is", "track is")
    ):
        return True
    return False


def _covenant_output_subjects(event: dict[str, Any]) -> set[str]:
    covenant = event.get("covenant") if isinstance(event.get("covenant"), dict) else {}
    subjects: set[str] = set()
    withheld = covenant.get("withheld") if isinstance(covenant.get("withheld"), list) else []
    for item in withheld:
        if not isinstance(item, dict):
            continue
        rule = str(item.get("rule") or "")
        subject = str(item.get("subject") or "")
        if subject and rule in {"do_not_reveal", "ignore", "coarsen"}:
            subjects.add(subject)
    applied = covenant.get("rules_applied") if isinstance(covenant.get("rules_applied"), list) else []
    for raw in applied:
        rule = str(raw)
        if ":" not in rule:
            continue
        verb, subject = rule.split(":", 1)
        if subject and verb in {"do_not_reveal", "ignore", "coarsen"}:
            subjects.add(subject)
    return subjects


def _covenant_withholds(event: dict[str, Any], subject: str) -> bool:
    covenant = event.get("covenant") if isinstance(event.get("covenant"), dict) else {}
    withheld = covenant.get("withheld") if isinstance(covenant.get("withheld"), list) else []
    if any(isinstance(item, dict) and item.get("subject") == subject for item in withheld):
        return True
    applied = covenant.get("rules_applied") if isinstance(covenant.get("rules_applied"), list) else []
    return any(
        str(rule).startswith(
            (
                f"do_not_reveal:{subject}",
                f"do_not_retain:{subject}",
                f"ignore:{subject}",
                f"coarsen:{subject}",
            )
        )
        for rule in applied
    )


def _memory_covenant_allows_content(trace: dict[str, Any]) -> bool:
    covenant = trace.get("covenant") if isinstance(trace.get("covenant"), dict) else {}
    if not covenant:
        return True
    withheld = covenant.get("withheld") if isinstance(covenant.get("withheld"), list) else []
    # A memory preview is condensed prose. It cannot safely prove that a
    # withheld subject was absent from that prose, so any source-level output
    # withholding keeps the whole preview local.
    if any(isinstance(item, dict) for item in withheld):
        return False
    applied = covenant.get("rules_applied") if isinstance(covenant.get("rules_applied"), list) else []
    return not any(
        str(rule).startswith(("do_not_reveal:", "ignore:", "coarsen:", "do_not_retain:memory"))
        for rule in applied
    )


def _safe_covenant(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, Any] = {}
    for key, limit in (("id", 160), ("name", 240), ("sha256", 160), ("version", 80)):
        text = _bounded_text(value.get(key), limit=limit)
        if text:
            result[key] = text
    extends = value.get("extends") if isinstance(value.get("extends"), list) else []
    safe_extends = [
        text for item in extends[:32] if (text := _bounded_text(item, limit=160))
    ]
    if safe_extends:
        result["extends"] = safe_extends
    commitments = value.get("commitments")
    if isinstance(commitments, int) and not isinstance(commitments, bool):
        result["commitments"] = max(0, min(commitments, 1_000_000))
    elif isinstance(commitments, list):
        # Event packets carry only the count. Covenant prose remains local.
        result["commitments"] = min(len(commitments), 1_000_000)
    applied = value.get("rules_applied") if isinstance(value.get("rules_applied"), list) else []
    safe_applied = [
        token for item in applied[:128] if (token := _safe_covenant_rule_token(item))
    ]
    if safe_applied:
        result["rules_applied"] = safe_applied
    withheld = value.get("withheld") if isinstance(value.get("withheld"), list) else []
    safe_withheld: list[dict[str, Any]] = []
    for item in withheld[:128]:
        if not isinstance(item, dict):
            continue
        entry: dict[str, Any] = {}
        rule = _safe_covenant_atom(item.get("rule"), limit=80)
        subject = _safe_covenant_atom(item.get("subject"), limit=160)
        if rule:
            entry["rule"] = rule
        if subject:
            entry["subject"] = subject
        count = _finite_number(item.get("count"))
        if count is not None and count >= 0:
            entry["count"] = count
        if entry:
            safe_withheld.append(entry)
    if safe_withheld:
        result["withheld"] = safe_withheld
    return result or None


def _safe_covenant_rule_token(value: Any) -> str | None:
    text = str(value or "").strip()
    if re.fullmatch(
        r"(?:do_not_reveal|do_not_retain|ignore|coarsen|max_window|quiet_hours|require_consent):[A-Za-z0-9._:-]+",
        text,
    ):
        return text[:500]
    return None


def _safe_covenant_atom(value: Any, *, limit: int) -> str | None:
    text = str(value or "").strip()
    return text[:limit] if re.fullmatch(r"[A-Za-z0-9._-]+", text) else None


def _time_range(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    start = _finite_number(value.get("start_s"))
    end = _finite_number(value.get("end_s"))
    if start is None or end is None or start < 0 or end < start:
        return None
    return {"start_s": start, "end_s": end}


def _finite_number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(float(value)):
        return None
    return value


def _bounded_text(value: Any, *, limit: int = 12_000) -> str | None:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return None
    text = " ".join(str(value).split()).strip()
    for pattern, replacement in _PRIVATE_TEXT_PATTERNS:
        text = pattern.sub(replacement, text)
    return text[:limit] if text else None


def _ref_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-")
    return token[:160] or "item"


_PRIVATE_TEXT_PATTERNS = (
    (re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s'\"<>]+", re.IGNORECASE), "[uri redacted]"),
    (
        re.compile(r"\bdata:(?:audio|application/octet-stream)[^\s'\"<>]*", re.IGNORECASE),
        "[uri redacted]",
    ),
    (
        re.compile(r"(?<![A-Za-z0-9._-])/(?!/)[^\s'\"<>)]*[^\s'\"<>).,;:!?]"),
        "[local path redacted]",
    ),
    (re.compile(r"(?:^|(?<=[\s'\"(]))~/[^\s'\"<>)]*"), "[local path redacted]"),
    (re.compile(r"\b[A-Za-z]:\\[^\s'\"<>]+", re.IGNORECASE), "[local path redacted]"),
    (re.compile(r"\\\\[^\\\s'\"<>]+\\[^\s'\"<>]+"), "[local path redacted]"),
)
