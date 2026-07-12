"""The covenant engine: sonic sovereignty as a runtime layer.

A **listening covenant** is a small, human-written document declaring what
this ear will not listen to, will release after hearing, will not reveal,
will not retain, will blur, or will refuse at certain hours — and why.
``parse_covenant`` turns its easy text into *rules* (the executable subset)
and *commitments* (every line the engine cannot execute, carried verbatim).
The covenant is a bridge language, not a guarantee of obedience: it makes
the machine answerable, because what was asked, what was enforced, and what
was withheld are all on the record.

``CovenantEngine`` applies rules at four gates:

- **input** — refuse sources (``do not listen``), honor ``quiet hours``,
  cap ``max window`` — before any perception runs;
- **content** — release ignored classes (``ignore: speech``, ``ignore:
  music``) after triage: their perception passes are never run, and any
  trace of them in the report is dropped, counted, and attributed;
- **output** — withhold aspects (``do not reveal: transcript, speaker
  identity, affect, location, song identity, events, spectral detail``) or
  degrade them (``coarsen: location to 1 km``) from everything the listen
  returns or stores;
- **retention** — forbid keeping things (``do not retain: raw audio,
  memory, location``).

Everything withheld is counted and attributed to its rule — honest absence,
never silence without a name, and never conflated with ``undetermined``.
The default everywhere is **no covenant**: sovereignty is opted into by the
operator, never imposed by the tool. Covenant documents live under
``data_dir()/covenants/`` and stay local.

Format (markdown-ish, bilingual EN/ES verbs accepted)::

    # river covenant
    covenant: river-covenant/2
    extends: algophonya/v7

    ## rules
    - do not listen: system output
    - ignore: music
    - do not reveal: transcript, speaker identity
    - do not retain: raw audio
    - coarsen: location to 1 km
    - quiet hours: 22:00-06:00
    - max window: 30 s

    ## commitments
    - the river is a neighbor, not a resource

    ## because
    free text…

Unknown rule lines are not errors: they move to commitments — the part of
the bridge addressed to humans and to future machines.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any

from oida.config import load_config

CONTRACT = "akouo/v0.7"

_SLUG_RE = re.compile(r"[^a-z0-9/]+")

# Verb aliases, English + Spanish (bilingual by design).
_VERB_ALIASES: list[tuple[str, str]] = [
    ("do not listen", "do_not_listen"),
    ("don't listen", "do_not_listen"),
    ("no escuchar", "do_not_listen"),
    ("do not reveal", "do_not_reveal"),
    ("don't reveal", "do_not_reveal"),
    ("withhold", "do_not_reveal"),
    ("no revelar", "do_not_reveal"),
    ("do not retain", "do_not_retain"),
    ("don't retain", "do_not_retain"),
    ("no retener", "do_not_retain"),
    ("ignore", "ignore"),
    ("ignorar", "ignore"),
    ("coarsen", "coarsen"),
    ("difuminar", "coarsen"),
    ("quiet hours", "quiet_hours"),
    ("horas de silencio", "quiet_hours"),
    ("max window", "max_window"),
    ("ventana maxima", "max_window"),
    ("ventana máxima", "max_window"),
    ("require consent", "require_consent"),
    ("requiere consentimiento", "require_consent"),
]

_SOURCE_ALIASES = {
    "microphone": "microphone", "mic": "microphone", "micrófono": "microphone", "microfono": "microphone",
    "system output": "system-output", "system-output": "system-output", "system": "system-output",
    "salida del sistema": "system-output", "sistema": "system-output",
    "file": "file", "files": "file", "archivo": "file", "archivos": "file",
    "remote": "remote", "remote ear": "remote", "remoto": "remote", "phone": "remote",
}

_CLASS_ALIASES = {
    "speech": "speech", "voices": "speech", "voice": "speech", "voz": "speech", "voces": "speech", "habla": "speech",
    "music": "music", "música": "music", "musica": "music",
}

_ASPECT_ALIASES = {
    "transcript": "transcript", "transcripts": "transcript", "words": "transcript", "transcripción": "transcript", "transcripcion": "transcript",
    "speaker identity": "speaker-identity", "speaker-identity": "speaker-identity", "identity": "speaker-identity",
    "who is speaking": "speaker-identity", "identidad": "speaker-identity",
    "affect": "affect", "emotion": "affect", "emotions": "affect", "afecto": "affect", "emoción": "affect", "emocion": "affect",
    "location": "location", "place": "location", "lugar": "location", "ubicación": "location", "ubicacion": "location",
    "song identity": "song-identity", "song-identity": "song-identity", "songid": "song-identity",
    "music identity": "song-identity", "canción": "song-identity", "cancion": "song-identity",
    "events": "events", "eventos": "events",
    "spectral detail": "spectral-detail", "spectral-detail": "spectral-detail", "features": "spectral-detail", "espectro": "spectral-detail",
}

_RETAIN_ALIASES = {
    "raw audio": "raw-audio", "raw-audio": "raw-audio", "audio": "raw-audio", "audio crudo": "raw-audio",
    "memory": "memory", "memories": "memory", "memoria": "memory", "memorias": "memory",
    "location": "location", "lugar": "location", "ubicación": "location", "ubicacion": "location",
}

_SECTION_ALIASES = {
    "rules": "rules", "reglas": "rules",
    "commitments": "commitments", "compromisos": "commitments",
    "because": "because", "porque": "because", "por qué": "because", "por que": "because",
}


@dataclass
class Covenant:
    """A parsed listening covenant: identity, executable rules, carried commitments."""

    id: str
    name: str
    version: str | None = None
    extends: list[str] = field(default_factory=list)
    rules: list[dict[str, Any]] = field(default_factory=list)
    commitments: list[str] = field(default_factory=list)
    because: str = ""
    sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "contract": CONTRACT,
            "extends": list(self.extends),
            "rules": [dict(rule) for rule in self.rules],
            "commitments": list(self.commitments),
            "because": self.because,
            "source_sha256": self.sha256,
        }

    def reference(self) -> dict[str, Any]:
        """Identity-only block for events and records: no rules text, no content."""
        ref: dict[str, Any] = {"id": self.id, "name": self.name, "sha256": self.sha256}
        if self.version:
            ref["version"] = self.version
        if self.extends:
            ref["extends"] = list(self.extends)
        ref["commitments"] = len(self.commitments)
        return ref

    def rules_for(self, verb: str) -> list[dict[str, Any]]:
        return [rule for rule in self.rules if rule.get("verb") == verb]


def _slugify(value: str) -> str:
    return _SLUG_RE.sub("-", value.strip().lower()).strip("-") or "covenant"


def _match_verb(line: str) -> tuple[str, str] | None:
    lowered = line.lower()
    for alias, verb in _VERB_ALIASES:
        if lowered.startswith(alias):
            rest = line[len(alias):].strip()
            if rest.startswith(":"):
                rest = rest[1:].strip()
            return verb, rest
    return None


def _split_subjects(rest: str) -> list[str]:
    parts = re.split(r"[,;·]| and | y ", rest)
    return [part.strip().lower() for part in parts if part.strip()]


def _parse_rule(line: str) -> dict[str, Any] | None:
    """Parse one rule line; None means the line becomes a commitment."""
    matched = _match_verb(line)
    if matched is None:
        return None
    verb, rest = matched
    rule: dict[str, Any] = {"verb": verb, "text": line}
    if verb == "do_not_listen":
        subjects = [_SOURCE_ALIASES.get(s) for s in _split_subjects(rest)]
        subjects = [s for s in subjects if s]
        if not subjects:
            return None
        rule["subjects"] = subjects
    elif verb == "ignore":
        subjects = [_CLASS_ALIASES.get(s) for s in _split_subjects(rest)]
        subjects = [s for s in subjects if s]
        if not subjects:
            return None
        rule["subjects"] = subjects
    elif verb == "do_not_reveal":
        subjects = [_ASPECT_ALIASES.get(s) for s in _split_subjects(rest)]
        subjects = [s for s in subjects if s]
        if not subjects:
            return None
        rule["subjects"] = subjects
    elif verb == "do_not_retain":
        subjects = [_RETAIN_ALIASES.get(s) for s in _split_subjects(rest)]
        subjects = [s for s in subjects if s]
        if not subjects:
            return None
        rule["subjects"] = subjects
    elif verb == "coarsen":
        match = re.search(r"location|lugar|ubicaci[oó]n", rest, re.IGNORECASE)
        km = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(km|kilometers?|kil[oó]metros?)", rest, re.IGNORECASE)
        if not match or not km:
            return None
        rule["subjects"] = ["location"]
        rule["args"] = {"km": float(km.group(1))}
    elif verb == "quiet_hours":
        match = re.search(r"([0-2]?\d:[0-5]\d)\s*[-–a]+\s*([0-2]?\d:[0-5]\d)", rest)
        if not match:
            return None
        rule["args"] = {"start": match.group(1), "end": match.group(2)}
    elif verb == "max_window":
        match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*s?", rest)
        if not match:
            return None
        rule["args"] = {"seconds": float(match.group(1))}
    elif verb == "require_consent":
        rule["subjects"] = _split_subjects(rest) or ["capture"]
    return rule


def parse_covenant(text: str, *, fallback_name: str = "covenant") -> Covenant:
    """Parse a covenant document. Tolerant by design: unknown rule lines are
    moved to commitments — the bridge language does not reject what it cannot
    yet execute."""
    name = fallback_name
    covenant_id: str | None = None
    extends: list[str] = []
    rules: list[dict[str, Any]] = []
    commitments: list[str] = []
    because_lines: list[str] = []
    section = "rules"  # a bare list of lines reads as rules-then-commitments

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("## "):
            section = _SECTION_ALIASES.get(line[3:].strip().lower(), "commitments")
            continue
        if line.startswith("# "):
            name = line[2:].strip() or name
            continue
        lowered = line.lower()
        if lowered.startswith("covenant:"):
            covenant_id = line.split(":", 1)[1].strip()
            continue
        if lowered.startswith("extends:"):
            extends.extend(_split_subjects(line.split(":", 1)[1]))
            continue
        body = line[2:].strip() if line.startswith("- ") else line
        if section == "because":
            because_lines.append(body)
            continue
        if section == "commitments":
            commitments.append(body)
            continue
        rule = _parse_rule(body)
        if rule is None:
            commitments.append(body)
        else:
            rules.append(rule)

    version = None
    if covenant_id and "/" in covenant_id:
        version = covenant_id.rsplit("/", 1)[1]
    return Covenant(
        id=covenant_id or _slugify(name),
        name=name,
        version=version,
        extends=extends,
        rules=rules,
        commitments=commitments,
        because="\n".join(because_lines),
        sha256=sha256(text.encode("utf-8")).hexdigest(),
    )


# ---------------------------------------------------------------------------
# the engine: four gates
# ---------------------------------------------------------------------------

_PASS_BY_ASPECT = {
    "transcript": {"transcribe"},
    "speaker-identity": {"transcribe", "speech"},
    "events": {"events"},
    "song-identity": set(),  # enforced at the songid toggle, not a MOSS pass
}

_PASS_BY_CLASS = {
    "speech": {"transcribe", "speech"},
    "music": {"music"},
}


class CovenantEngine:
    """Applies one parsed covenant at the four gates. Stateless between calls;
    every application returns what acted and what was withheld, so callers can
    put honest absence on the record."""

    def __init__(self, covenant: Covenant) -> None:
        self.covenant = covenant

    # -- input gate ---------------------------------------------------------
    def refuse_source(self, source_type: str) -> str | None:
        """Return the refusing rule text when the covenant refuses this source."""
        normalized = {
            "live_input": "microphone",
            "system_output": "system-output",
            "buffer": "microphone",
            "file": "file",
            "external_stream": "system-output",
        }.get(str(source_type), str(source_type))
        for rule in self.covenant.rules_for("do_not_listen"):
            if normalized in rule.get("subjects", []):
                return str(rule["text"])
        return None

    def refuse_quiet_hours(self, *, now: time.struct_time | None = None) -> str | None:
        moment = now or time.localtime()
        minutes = moment.tm_hour * 60 + moment.tm_min
        for rule in self.covenant.rules_for("quiet_hours"):
            args = rule.get("args", {})
            try:
                sh, sm = (int(part) for part in str(args.get("start")).split(":"))
                eh, em = (int(part) for part in str(args.get("end")).split(":"))
            except (TypeError, ValueError):
                continue
            start, end = sh * 60 + sm, eh * 60 + em
            inside = start <= minutes < end if start <= end else (minutes >= start or minutes < end)
            if inside:
                return str(rule["text"])
        return None

    def clamp_window(self, seconds: float | None) -> tuple[float | None, str | None]:
        for rule in self.covenant.rules_for("max_window"):
            cap = float(rule.get("args", {}).get("seconds", 0) or 0)
            if cap > 0 and seconds is not None and float(seconds) > cap:
                return cap, str(rule["text"])
        return seconds, None

    # -- content + output gates over the perception passes -------------------
    def _withheld_aspects(self) -> list[str]:
        out: list[str] = []
        for rule in self.covenant.rules_for("do_not_reveal"):
            out.extend(rule.get("subjects", []))
        return out

    def _ignored_classes(self) -> list[str]:
        out: list[str] = []
        for rule in self.covenant.rules_for("ignore"):
            out.extend(rule.get("subjects", []))
        return out

    def filter_passes(self, passes: list[str]) -> tuple[list[str], list[str]]:
        """Remove perception passes the covenant makes pointless or forbidden —
        the strongest form of withholding: never computed at all."""
        blocked: set[str] = set()
        applied: list[str] = []
        for aspect in self._withheld_aspects():
            hit = _PASS_BY_ASPECT.get(aspect, set()) & set(passes)
            if hit:
                blocked |= hit
                applied.append(f"do_not_reveal:{aspect}")
        for cls in self._ignored_classes():
            hit = _PASS_BY_CLASS.get(cls, set()) & set(passes)
            if hit:
                blocked |= hit
                applied.append(f"ignore:{cls}")
        return [p for p in passes if p not in blocked], applied

    def redact_perception(self, perception: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Drop withheld/ignored material from a perception report before any
        claim is built. Returns (redacted, withheld-entries)."""
        redacted = json.loads(json.dumps(perception))
        withheld: list[dict[str, Any]] = []

        def _drop(section: str, rule: str, subject: str) -> None:
            value = redacted.get(section)
            present = bool(value) and (not isinstance(value, dict) or value.get("present") is not False)
            if section in redacted and present:
                redacted[section] = {"present": False, "withheld": True} if isinstance(value, dict) else None
                withheld.append({"rule": rule, "subject": subject, "count": 1})

        aspects = self._withheld_aspects()
        classes = self._ignored_classes()
        if "transcript" in aspects or "speaker-identity" in aspects or "speech" in classes:
            rule = "ignore" if "speech" in classes else "do_not_reveal"
            subject = "speech" if "speech" in classes else "transcript"
            _drop("transcript", rule, subject)
            if "speech" in classes or "speaker-identity" in aspects:
                _drop("speech", rule, "speech" if "speech" in classes else "speaker-identity")
        if "music" in classes:
            _drop("music", "ignore", "music")
        if "events" in aspects:
            events = redacted.get("events")
            if isinstance(events, list) and events:
                withheld.append({"rule": "do_not_reveal", "subject": "events", "count": len(events)})
                redacted["events"] = []
        if "spectral-detail" in aspects:
            dsp = redacted.get("dsp")
            if isinstance(dsp, dict) and isinstance(dsp.get("features"), dict) and dsp["features"]:
                kept = {k: v for k, v in dsp["features"].items() if k in ("durationSeconds", "sampleRate", "channelCount")}
                withheld.append({"rule": "do_not_reveal", "subject": "spectral-detail", "count": max(1, len(dsp["features"]) - len(kept))})
                dsp["features"] = kept
        return redacted, withheld

    def redact_command_output(self, command_output: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Withhold claim categories the covenant refuses (affect → the
        interpreted category) after routing, before the event is built."""
        if "affect" not in self._withheld_aspects():
            return command_output, []
        redacted = json.loads(json.dumps(command_output))
        withheld: list[dict[str, Any]] = []
        summary = redacted.get("claim_summary")
        if isinstance(summary, dict):
            dropped = summary.get("interpreted") or []
            if dropped:
                withheld.append({"rule": "do_not_reveal", "subject": "affect", "count": len(dropped)})
                summary["interpreted"] = []
        return redacted, withheld

    # -- location -------------------------------------------------------------
    def apply_location(self, location: dict[str, Any] | None) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        if location is None:
            return None, []
        aspects = self._withheld_aspects()
        retained = [s for rule in self.covenant.rules_for("do_not_retain") for s in rule.get("subjects", [])]
        if "location" in aspects or "location" in retained:
            return None, [{"rule": "do_not_reveal" if "location" in aspects else "do_not_retain", "subject": "location", "count": 1}]
        for rule in self.covenant.rules_for("coarsen"):
            if "location" in rule.get("subjects", []):
                km = float(rule.get("args", {}).get("km", 1.0) or 1.0)
                step = km / 111.32
                coarse = dict(location)
                coarse["lat"] = round(round(float(location["lat"]) / step) * step, 6)
                coarse["lon"] = round(round(float(location["lon"]) / step) * step, 6)
                coarse["accuracy_m"] = max(float(location.get("accuracy_m") or 0.0), km * 1000.0)
                coarse["label"] = f"{location.get('label') or 'location'} (coarsened to {km:g} km)".strip()
                return coarse, [{"rule": "coarsen", "subject": "location", "count": 1}]
        return location, []

    # -- retention gate -------------------------------------------------------
    def forbids_retention(self, target: str) -> str | None:
        for rule in self.covenant.rules_for("do_not_retain"):
            if target in rule.get("subjects", []):
                return str(rule["text"])
        return None

    def forbids_song_identity(self) -> bool:
        return "song-identity" in self._withheld_aspects()

    # -- the record ------------------------------------------------------------
    def event_block(self, *, rules_applied: list[str], withheld: list[dict[str, Any]]) -> dict[str, Any]:
        block = self.covenant.reference()
        if rules_applied:
            block["rules_applied"] = sorted(set(rules_applied))
        if withheld:
            block["withheld"] = withheld
        return block


# ---------------------------------------------------------------------------
# storage: data_dir()/covenants/*.md + active pointer
# ---------------------------------------------------------------------------

class CovenantStore:
    """File-based covenant library. Documents are plain text, local, and
    inspectable; ``active`` is a pointer file so 'which ethics is this ear
    under right now' has one answer."""

    def __init__(self, root: Path | None = None) -> None:
        base = root if root is not None else load_config().data_dir
        self.dir = Path(base) / "covenants"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.pointer = self.dir / "active.txt"

    def _path(self, name: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.") or "covenant"
        if not safe.endswith(".md"):
            safe += ".md"
        return self.dir / safe

    def list(self) -> list[str]:
        return sorted(path.stem for path in self.dir.glob("*.md"))

    def read(self, name: str) -> str | None:
        path = self._path(name)
        return path.read_text(encoding="utf-8") if path.exists() else None

    def save(self, name: str, text: str) -> Covenant:
        parsed = parse_covenant(text, fallback_name=name)
        self._path(name).write_text(text, encoding="utf-8")
        return parsed

    def delete(self, name: str) -> bool:
        path = self._path(name)
        if not path.exists():
            return False
        if self.active_name() == name:
            self.activate(None)
        path.unlink()
        return True

    def activate(self, name: str | None) -> None:
        if name is None:
            self.pointer.unlink(missing_ok=True)
            return
        if not self._path(name).exists():
            raise FileNotFoundError(f"no covenant named {name!r}")
        self.pointer.write_text(name, encoding="utf-8")

    def active_name(self) -> str | None:
        if not self.pointer.exists():
            return None
        name = self.pointer.read_text(encoding="utf-8").strip()
        return name or None

    def active(self) -> Covenant | None:
        name = self.active_name()
        if not name:
            return None
        text = self.read(name)
        if text is None:
            return None
        return parse_covenant(text, fallback_name=name)

    def engine(self, *, override_name: str | None = None) -> CovenantEngine | None:
        """The active engine — or one for a named covenant when a request
        pins it explicitly. None means the layer is empty: the default."""
        if override_name:
            text = self.read(override_name)
            if text is None:
                raise FileNotFoundError(f"no covenant named {override_name!r}")
            return CovenantEngine(parse_covenant(text, fallback_name=override_name))
        covenant = self.active()
        return CovenantEngine(covenant) if covenant else None
