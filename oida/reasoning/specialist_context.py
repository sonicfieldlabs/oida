"""Small, locator-free views of retained specialist observations."""

import math
from oida.reasoning.evidence import safe_external_text


def project(lanes):
    output = []
    if not isinstance(lanes, list):
        return output
    for lane in lanes[:12]:
        if not isinstance(lane, dict) or lane.get("status") != "complete":
            continue
        evidence = lane.get("evidence")
        task = lane.get("task")
        if not isinstance(evidence, dict):
            continue
        result = evidence.get("result")
        if not isinstance(result, dict):
            continue
        if task not in {"tag_events", "track_beats", "transcribe", "speech_quality"}:
            continue
        data = {}
        if task == "transcribe":
            data["text"] = safe_external_text(result.get("text"), limit=2000)
        elif task == "tag_events":
            data["labels"] = [
                dict(
                    label=safe_external_text(x.get("label"), limit=100),
                    score=x.get("score"),
                )
                for x in (
                    result.get("labels")
                    if isinstance(result.get("labels"), list)
                    else []
                )[:10]
                if isinstance(x, dict)
                and isinstance(x.get("score"), (int, float))
                and not isinstance(x["score"], bool)
                and math.isfinite(x["score"]) and 0 <= x["score"] <= 1
            ]
        elif task == "speech_quality":
            data = {key: value for key in ("sig", "bak", "ovrl")
                    if isinstance(value := result.get(key), (int, float))
                    and not isinstance(value, bool) and math.isfinite(value) and 1 <= value <= 5}
            data["limitation"] = "Uncalibrated speech-domain quality hypothesis; not a general sound-quality measurement"
        else:
            data["beats_seconds"] = [
                x
                for x in (
                    result.get("beats_seconds")
                    if isinstance(result.get("beats_seconds"), list)
                    else []
                )[:100]
                if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x >= 0
            ]
        output.append(
            dict(
                task=task,
                result=data,
                deployment_id=safe_external_text(
                    evidence.get("deployment_id"), limit=120
                ),
                model_revision=safe_external_text(
                    evidence.get("model_revision"), limit=100
                ),
                status=safe_external_text(result.get("status"), limit=40),
                basis="Retained model hypothesis; no fresh measurement and no causal proof",
            )
        )
    return output
