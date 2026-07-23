# Oída gateway contract

Oída is both a listening agent and a local listening layer for other agents.
Installing Oída installs and exposes the complete stack: AKOÚŌ routing and
claim discipline, Earworm event/provenance envelopes, and the Akousmata store
and navigator.

The stable gateway contract is `oida/gateway/v0.4`. It supports two paths:

1. **Oída-owned perception** — pass Oída a local audio path. Its configured
   engine (MOSS-Audio when available, DSP-only stub otherwise) performs the
   perceptual passes. Oída returns the perception report, AKOÚŌ command output,
   normalized listening event, and an optional memory trace.
2. **Host-supplied perception** — an audio-capable Hermes, Codex, Claude, or
   generic host describes what its active model heard using
   `oida/host-perception/v0.3`. Oída does not run MOSS again. It applies the
   same router, evidence permissions, claim taxonomy, Earworm provenance, and
   Akousmata memory flow.

Host perception must declare its apparatus when known. Sample rate, channel
count, bandwidth, calibration, preprocessing, and blind spots determine which
claims can be supported. An undeclared apparatus is accepted but explicitly
marked undetermined. Model output can never become a `measured` claim merely
because the model used a number; measurements need DSP, metadata, a measuring
tool, or a declared human measurement.

Both paths emit `oida/listening-event/v0.2` with an
`akouo/listening-context/v1` block. The context answers different questions
than the apparatus: position says where the listener stands in relation to the
object; apertures say which evidence openings were actually available;
auditory scales say at what temporal or infrastructural resolution it listened;
participants keep plural hearing attributable; honest absences say what was
unavailable, withheld, refused, or not retained; action authority says what the
runtime may do. A capable host may declare `execute_scoped`, but that declaration
is only attributed input—the OÍDA hearing boundary remains `observe_only` until
a separate explicit action, such as Remember, grants a narrow scope and yields
a receipt.

Before direct host perception, the host inspects the active Covenant and reads
the bounded `LISTENING.md`. The Covenant governs what the host may listen to,
retain, or reveal; the identity may only orient attention and voice. A host
that applied the identity declares the digest it used. Oída records a matching
revision as host-declared provenance, reports missing or changed revisions,
and never treats identity text as evidence. Oída-owned perception snapshots
the same file for the whole multi-pass event.

## Lifecycle

- `oida` or `oida serve` runs the agent, REST gateway, dashboard, and mounted
  Akousmata navigator in one process.
- `oida start` ensures the singleton local gateway is running in the background.
- `oida gateway --stdio --ensure-daemon` is the MCP command for local agents.
- `oida agent` ensures the gateway and opens its local interface.
- `oida status`, `oida doctor`, and `oida stop` inspect or control the managed
  gateway.

Hermes, Codex, and Claude integrations always invoke the stdio gateway command,
so they can start Oída when needed without loading a second MOSS model. A
running daemon is reused. The remote capture page uses the same process, but
OÍDA does not publish it or configure network access. Any non-loopback
deployment must provide its own authenticated HTTPS boundary.

## Host input example

```json
{
  "contract": "oida/host-perception/v0.3",
  "host": {
    "id": "codex",
    "model": "audio-capable-model",
    "session_id": "session-123",
    "audio_input_capable": true
  },
  "listening_identity": {
    "contract": "oida/listening-identity/v0.1",
    "sha256": "718835e68333f5fca24863afda54fc6258f28c7158d1aa24578b95abfb8f811d",
    "applied": true
  },
  "source": {
    "label": "attached field recording",
    "type": "file",
    "duration_s": 18.4,
    "audio_available_to_oida": false
  },
  "apparatus": {
    "substrate": "host_audio_model",
    "sample_rate_hz": 48000,
    "channels": 2,
    "bandwidth_limit_hz": 24000,
    "known_blind_spots": ["The host does not expose its resampling path."]
  },
  "listening_context": {
    "contract": "akouo/listening-context/v1",
    "position": {
      "relation_to_object": "host model inspecting an attached recording",
      "limitations": ["The gateway does not receive the raw audio."]
    },
    "apertures": [{
      "id": "host-audio",
      "kind": "direct_audio",
      "status": "available",
      "limits": ["Host preprocessing is opaque."]
    }],
    "auditory_scales": ["event", "scene"],
    "sources_of_listening": ["audio", "model"],
    "participants": [{
      "id": "codex",
      "type": "agent",
      "role": "host perceptual listener",
      "report_ref": "#/observations"
    }],
    "action_authority": {
      "mode": "observe_only",
      "scopes": ["describe"],
      "requires_confirmation": true,
      "reversible": true
    },
    "honest_absences": []
  },
  "observations": [
    {
      "statement": "A repeating metallic impact is audible.",
      "category": "heard",
      "confidence": "medium",
      "source": "model",
      "time_range": {"start_s": 2.1, "end_s": 7.8}
    }
  ],
  "uncertainty": ["The source object cannot be identified from audio alone."]
}
```

See `oida/schemas/host-perception.schema.json` for the complete input schema
and `oida/schemas/listening-event.schema.json` for the output boundary. A
running gateway publishes those schemas plus AKOÚŌ's canonical context schema
under `/gateway/schema/*`; OÍDA references the AKOÚŌ schema instead of forking
its semantic owner.
The digest in this example is illustrative; use the exact value returned by
`GET /listening` or `oida_listening_identity(action="read")`.
