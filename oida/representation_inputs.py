"""Exact admitted inscriptions and declared parser projections; never audio I/O."""

from copy import deepcopy
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import struct

from akousma import new_id
from akousma.representations import representation_errors, representation_record_errors

MAX_SOURCE_BYTES = 1_048_576
CONVENTIONS = {
    "text": "oida/plain-text/v1",
    "data": "oida/json/v1",
    "morse": "oida/morse-latin/v1",
    "notation": "oida/notation-text/v1",
    "glyphs": "oida/glyph-inscription/v1",
    "code": "oida/code-inscription/v1",
    "midi": "oida/midi-events/v1",
}
_MORSE = dict(
    zip(
        ".- -... -.-. -.. . ..-. --. .... .. .--- -.- .-.. -- -. --- .--. --.- .-. ... - ..- ...- .-- -..- -.-- --.. ----- .---- ..--- ...-- ....- ..... -.... --... ---.. ----.".split(),
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
    )
)


class UnsupportedRepresentation(ValueError):
    pass


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise UnsupportedRepresentation("Duplicate JSON key")
        result[key] = value
    return result


def _exact_json_number(value):
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise UnsupportedRepresentation("Unsupported JSON decimal exponent") from None
    if not number.is_finite():
        raise UnsupportedRepresentation("Nonfinite JSON number")
    return number


def strict_json(text):
    """Validate numbers exactly; the provider still receives original JSON text.

    Decimal validation has no binary-float overflow or underflow. Exponents
    outside the decimal implementation's supported range fail admission.
    """
    return json.loads(
        text,
        object_pairs_hook=_unique,
        parse_float=_exact_json_number,
        parse_int=_exact_json_number,
        parse_constant=lambda _: (_ for _ in ()).throw(
            UnsupportedRepresentation("Nonfinite JSON number")
        ),
    )


def representation(
    material: bytes,
    *,
    identity=None,
    form="text",
    media_type="text/plain",
    terms_ref,
    source_class="authored_work",
    origin=None,
    convention=None,
    time_basis=None,
):
    if not isinstance(material, bytes) or len(material) > MAX_SOURCE_BYTES:
        raise ValueError(
            "Exact source bytes within the declared one MiB limit are required"
        )
    if form not in CONVENTIONS:
        raise UnsupportedRepresentation(
            "No inscription adapter for this representation"
        )
    if form != "midi":
        material.decode("utf-8")
        if b"\0" in material:
            raise ValueError("Textual inscriptions cannot contain NUL")
    key = identity or new_id("rep")
    rep = dict(
        contract="earworm/representation/v1",
        representation_id=key,
        subject_ref=key,
        form=form,
        media_type=media_type,
        origin=deepcopy(
            origin or dict(kind="authored", domain="non_acoustic", source_refs=[])
        ),
        source_class=source_class,
        artifact=dict(
            state="retained",
            ref="earworm:representation:" + key,
            sha256=sha256(material).hexdigest(),
            byte_count=len(material),
        ),
        preservation=dict(layout="preserved", losses=[]),
        convention=deepcopy(
            convention or dict(status="declared", id=CONVENTIONS[form], revision="1")
        ),
        time_basis=deepcopy(
            time_basis
            or [
                dict(
                    kind="sequence",
                    unit="byte" if form == "midi" else "unicode_codepoint",
                )
            ]
        ),
        terms=dict(status="declared", ref=terms_ref),
    )
    if form != "midi":
        rep["encoding"] = "utf-8"
    errors = representation_errors(rep)
    if errors:
        raise ValueError("; ".join(errors))
    return rep


def read_file(path, *, allowed_roots, **metadata):
    """Owner-selected local roots, exact bytes, no URL or audio conversion."""
    source = Path(path).expanduser().resolve(strict=True)
    if not source.is_file() or not any(
        source.is_relative_to(Path(p).expanduser().resolve(strict=True))
        for p in allowed_roots
    ):
        raise PermissionError("Source is outside the owner-selected roots")
    with source.open("rb") as stream:
        material = stream.read(MAX_SOURCE_BYTES + 1)
    rep = representation(material, **metadata)
    return rep, material


def _midi(data):
    if len(data) < 14 or data[:4] != b"MThd" or struct.unpack(">I", data[4:8])[0] != 6:
        raise UnsupportedRepresentation("Unsupported MIDI header")
    format_id, tracks, division = struct.unpack(">HHH", data[8:14])
    if (
        format_id not in (0, 1)
        or not 1 <= tracks <= 32
        or (format_id == 0 and tracks != 1)
        or division == 0
        or division & 0x8000
    ):
        raise UnsupportedRepresentation(
            "MIDI adapter requires format 0/1 and PPQ timing"
        )
    offset, events, end_tick = 14, [], 0
    for track in range(tracks):
        if data[offset : offset + 4] != b"MTrk" or offset + 8 > len(data):
            raise UnsupportedRepresentation("Missing MIDI track")
        length = struct.unpack(">I", data[offset + 4 : offset + 8])[0]
        offset += 8
        end = offset + length
        if end > len(data):
            raise UnsupportedRepresentation("Truncated MIDI track")
        tick, running, ended = 0, None, False

        def vlq():
            nonlocal offset
            value = 0
            for _ in range(4):
                if offset >= end:
                    raise UnsupportedRepresentation(
                        "Truncated MIDI variable-length value"
                    )
                byte = data[offset]
                offset += 1
                value = (value << 7) | (byte & 127)
                if not byte & 128:
                    return value
            raise UnsupportedRepresentation("Oversized MIDI variable-length value")

        while offset < end:
            tick += vlq()
            if offset >= end:
                raise UnsupportedRepresentation("Missing MIDI event")
            status = data[offset]
            if status >= 128:
                offset += 1
            elif running is not None:
                status = running
            else:
                raise UnsupportedRepresentation("MIDI data has no running status")
            if 0x80 <= status <= 0xEF:
                running = status
                count = 1 if status >> 4 in (12, 13) else 2
                payload = data[offset : offset + count]
                offset += count
                if (
                    len(payload) != count
                    or offset > end
                    or any(b >= 128 for b in payload)
                ):
                    raise UnsupportedRepresentation("Invalid MIDI channel message")
                event = dict(
                    track=track,
                    tick=tick,
                    status=status,
                    channel=status & 15,
                    data=list(payload),
                )
            elif status in (0xFF, 0xF0, 0xF7):
                running = None
                meta_type = None
                if status == 0xFF:
                    if offset >= end:
                        raise UnsupportedRepresentation("Missing MIDI meta type")
                    meta_type = data[offset]
                    offset += 1
                    if meta_type >= 128:
                        raise UnsupportedRepresentation("Invalid MIDI meta type")
                size = vlq()
                payload = data[offset : offset + size]
                offset += size
                if len(payload) != size or offset > end:
                    raise UnsupportedRepresentation("Truncated MIDI meta/SysEx event")
                event = dict(
                    track=track, tick=tick, status=status, data_hex=payload.hex()
                )
                if meta_type is not None:
                    event["meta_type"] = meta_type
                if meta_type == 0x2F:
                    if size != 0 or offset != end:
                        raise UnsupportedRepresentation(
                            "Invalid or premature MIDI end-of-track"
                        )
                    ended = True
            else:
                raise UnsupportedRepresentation("Unsupported MIDI system event")
            events.append(event)
            if len(events) > 10000:
                raise UnsupportedRepresentation(
                    "MIDI event projection exceeds its declared limit"
                )
            end_tick = max(end_tick, tick)
        if not ended:
            raise UnsupportedRepresentation("MIDI track has no explicit end-of-track")
    if offset != len(data):
        raise UnsupportedRepresentation("Unaccounted bytes after MIDI tracks")
    value = dict(
        convention="oida/midi-events/v1", format=format_id, ppq=division, events=events
    )
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode(), end_tick


def prepare_input(
    rep, material: bytes, *, operation_ref, record_ref, role="subject", selection=None
):
    """Declare what the provider receives, independently of the exact source."""
    if (
        representation_errors(rep)
        or len(material) != rep["artifact"].get("byte_count")
        or sha256(material).hexdigest() != rep["artifact"].get("sha256")
    ):
        raise ValueError("Source bytes and representation identity disagree")
    convention = rep["convention"]
    if convention != dict(
        status="declared", id=CONVENTIONS.get(rep["form"]), revision="1"
    ):
        raise UnsupportedRepresentation("Source convention is unknown or unsupported")
    received, view, losses, method = material, rep, [], None
    if rep["form"] == "data":
        strict_json(material.decode("utf-8"))
    elif rep["form"] == "morse":
        try:
            words = material.decode("ascii").strip().split("/")
            received = (
                " ".join(
                    "".join(_MORSE[symbol] for symbol in word.split()) for word in words
                )
                + "\n"
            ).encode()
        except (UnicodeDecodeError, KeyError):
            raise UnsupportedRepresentation(
                "Morse symbols do not match the declared alphabet"
            ) from None
        losses = [
            "Encoded mark spacing is preserved only in the original source; no acoustic timing was supplied."
        ]
        method = "oida:morse-latin-decode:v1"
    elif rep["form"] == "midi":
        received, ticks = _midi(material)
        losses = [
            "The provider receives an event projection, not the original binary serialization or an acoustic performance."
        ]
        method = "oida:midi-events:v1"
    if selection is not None:
        if method or not isinstance(selection, list) or not selection:
            raise ValueError(
                "Explicit byte selection requires a direct textual inscription"
            )
        last, chunks = 0, []
        for interval in selection:
            if (
                set(interval) != {"start", "end"}
                or type(interval["start"]) is not int
                or type(interval["end"]) is not int
                or not last <= interval["start"] < interval["end"] <= len(material)
            ):
                raise ValueError("Invalid source selection")
            part = material[interval["start"] : interval["end"]]
            part.decode("utf-8")
            chunks.append(part)
            last = interval["end"]
        received = b"".join(chunks)
        if received != material:
            losses = ["Only the explicitly selected source ranges were submitted."]
            method = "oida:utf8-byte-selection:v1"
    representations, relations = [deepcopy(rep)], []
    if method:
        view = representation(
            received,
            form="text",
            terms_ref=rep["terms"]["ref"],
            source_class=rep["source_class"],
            origin=dict(
                kind="transformed",
                domain=rep["origin"]["domain"],
                source_refs=[rep["representation_id"]],
            ),
        )
        view["preservation"] = dict(layout="transformed", losses=losses)
        if rep["form"] == "midi":
            view["time_basis"] = [
                dict(kind="authored", unit="midi_tick", extent=dict(start=0, end=ticks))
            ]
        representations.append(view)
        relations.append(
            dict(
                relation_id=new_id("rel"),
                kind="transformed_from",
                from_ref=view["representation_id"],
                to_ref=rep["representation_id"],
                declared_by="oida:inscription-adapter",
                method_ref=method,
                losses=losses,
            )
        )
    access = dict(
        contract="earworm/representation-access/v1",
        access_id=new_id("acc"),
        operation_ref=operation_ref,
        representation_ref=rep["representation_id"],
        received_representation_ref=view["representation_id"],
        role=role,
        status="available",
        coverage=dict(
            kind="partial" if method else "full",
            unit="byte",
            ranges=deepcopy(selection)
            if method and selection is not None
            else [dict(start=0, end=len(material))]
            if method
            else [],
            omissions=losses,
        ),
    )
    block = dict(
        contract="earworm/representation-record/v1",
        record_ref=record_ref,
        operation_ref=operation_ref,
        representations=representations,
        accesses=[access],
        relations=relations,
        outputs=[],
        binding_refs=[],
        outcome=dict(
            status="pending",
            recorded_by="oida",
            reason="Admitted inscription; provider execution pending.",
        ),
    )
    errors = representation_record_errors(block)
    if errors:
        raise ValueError("; ".join(errors))
    return block, {
        rep["representation_id"]: material,
        view["representation_id"]: received,
    }
