"""Daemon-owned spectral decisions. Model prose cannot grant apparatus support."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from akousma.listening_contracts import listening_access_errors
from akouo_contract.spectral_gate import extended_spectrum_decision
from oida.dsp import inspect_path


def gate_spectral_request(
    path: Path,
    access: dict,
    request: dict,
    *,
    evidence_manifest: Path | None = None,
    prepare_bindings=None,
) -> dict:
    """Use source bytes and optional operator evidence; model input stays unknown.

    No caller declaration or aliased reference can grant runtime model support.
    """
    errors = listening_access_errors(access)
    if errors:
        raise ValueError("; ".join(errors))
    source = inspect_path(str(path))
    subject = "sha256:" + source["sha256"]
    if access["subject_ref"] != subject or request.get("subject_ref") != subject:
        raise ValueError("Apparatus declaration must address the actual source SHA-256")
    sampled = access["sampled_representation"]
    if sampled["status"] == "known" and (
        sampled["sample_rate_hz"] != source["sampleRate"]
        or sampled["channels"] != source["channelCount"]
    ):
        raise ValueError("Sampled representation differs from the actual source")
    checked = deepcopy(request)
    # Never accept the caller's assertion that evidence references are resolved.
    checked["resolved_refs"] = [subject]
    bindings = prepare_bindings(source) if prepare_bindings is not None else {}
    evidence = None
    if evidence_manifest is not None:
        from oida.apparatus_evidence import resolve_evidence

        evidence = resolve_evidence(
            evidence_manifest,
            access,
            bindings=bindings,
            preprocessing=request.get("preprocessing"),
        )
        checked["resolved_refs"].extend(evidence["resolved_refs"])
    if bindings:
        from oida.pass_provenance import audio_fingerprint

        if audio_fingerprint(path)["sha256"] != source["sha256"]:
            raise ValueError("Source changed during input preparation")
    effective_access = deepcopy(access)
    # Reference aliasing must not turn a source hash or operator declaration
    # into proof of the adapter's actual input capabilities.
    effective_access["model_input"] = {
        "status": "unknown",
        "reason": "Actual loaded-adapter input is not resolved by the daemon",
    }
    result = extended_spectrum_decision(
        effective_access,
        checked,
        actor="oida:apparatus-gate",
        validate_access=listening_access_errors,
    )
    result["evidence_basis"] = "source bytes inspected; model input remains unresolved"
    if evidence is not None:
        result["apparatus_evidence"] = evidence
    if bindings:
        result["input_bindings"] = bindings
        declared = access["model_input"]
        mismatches = []
        if declared["status"] == "known":
            for kind, binding in bindings.items():
                if binding.get("status") != "prepared":
                    continue
                actual = binding["receipt"]["effective_input"]
                for field in ("sample_rate_hz", "channels"):
                    if declared[field] != actual[field]:
                        mismatches.append(
                            f"{kind}: declared {field} differs from prepared input"
                        )
                if (
                    declared["model_ref"] != binding["model_ref"]
                    or declared["representation_ref"] != binding["representation_ref"]
                ):
                    mismatches.append(
                        f"{kind}: model or representation reference differs from prepared input"
                    )
                if (
                    declared["window_s"]["start"] != 0
                    or abs(declared["window_s"]["end"] - actual["duration_s"])
                    > 1 / actual["sample_rate_hz"]
                ):
                    mismatches.append(
                        f"{kind}: declared window differs from prepared whole-source input"
                    )
                if (
                    declared["effective_band_hz"]["upper"]
                    > actual["sample_rate_hz"] / 2
                ):
                    mismatches.append(
                        f"{kind}: declared effective band exceeds prepared Nyquist bound"
                    )
        if mismatches:
            result["support"] = "unsupported"
            result["decision"]["reason"] += "; " + "; ".join(mismatches)
        result["binding_limit"] = (
            "Prepared input identity is not calibrated capture or model spectral competence; positive permission remains unavailable"
        )
    declared = access["model_input"]
    if (
        bindings
        and all(
            b.get("status") == "prepared"
            and b["receipt"]["weights"].get("status") == "known"
            for b in bindings.values()
        )
        and not mismatches
        and declared["status"] == "known"
    ):
        required = set(
            declared["evidence_refs"]
            + declared["preprocessing_refs"]
            + [declared["model_ref"], declared["representation_ref"]]
        )
        # Every model/recipe reference must resolve through an exact operator file;
        # source aliases cannot satisfy this separate approval boundary.
        model_refs = set()
        if evidence is not None:
            model_refs = set(evidence["model_refs"])
        if required <= model_refs:
            permitted = extended_spectrum_decision(
                access,
                checked,
                actor="oida:apparatus-gate",
                validate_access=listening_access_errors,
            )
            result.update(permitted)
            result["binding_limit"] = (
                "Operator-approved scope bound to prepared execution; not independent calibration or semantic validation"
            )
            if result["measurement_permitted"]:
                result["execution_bindings"] = {
                    k: b["binding_id"] for k, b in bindings.items()
                }
    return result
