# OÍDA architecture

OÍDA is one local process with several clients, not a collection of competing
apps. The FastAPI daemon owns listening state and exposes the same contracts to
the dashboard, native macOS shell, CLI, MCP clients, host integrations, and the
embedded Akousmata library.

## Runtime map

```text
audio file / live capture / declared host perception
                        |
                        v
                OÍDA gateway v0.4
                        |
          +-------------+--------------+
          |                            |
          v                            v
 deterministic DSP            optional perception engine
          |                    (local MOSS or configured API)
          +-------------+--------------+
                        |
                        v
          AKOÚŌ route + evidence permissions
                        |
                        v
        accountable immutable listening event
       (position · apertures · claims · authority)
          +-------------+--------------+
          |             |              |
          v             v              v
      session UI   Earworm context   explicit remember
                                         |
                                         v
                                  Akousmata / akousma
```

## Package boundaries

| Package | Owns | Does not own |
| --- | --- | --- |
| `akouo-contract` | Semantic vocabulary: claims, accountable context, skills, commands, presets, covenants, and routing. | Audio decoding, model inference, persistence, or UI state. |
| `akousma` (Earworm) | Addressable auditum, open sonic-memory record, events, lineage, disagreement, absence, action receipts, and store operations. | Listening policy or application UI. |
| `akousmata` | Rendering and structural audit of accountable memory: library, graph, map, timeline, wiki, research, consent/export, and human annotation. | Audio perception or another producer's listening block. |
| `sonicfield-oida` | Gateway lifecycle, capture, DSP, optional model adapters, AKOÚŌ execution, sessions, conversations, integrations, and app surfaces. | Hidden provider credentials, automatic cloud fallback, or implicit durable memory. |

OÍDA installs the canonical packages and loads their contracts. It does not
copy or fork them.

## Perception paths

### OÍDA-owned audio

`POST /gateway/listen` and `POST /listen-event` accept audio available to
the daemon. OÍDA performs one deterministic inspection and then invokes only
the perception passes selected by the route. The result declares its
apparatus, evidence level, and blind spots.

### Host-owned perception

`POST /gateway/harness` accepts an `oida/host-perception/v0.3` observation from
an audio-capable host. Host observations remain model evidence; they are never
relabeled as measured DSP. A host preflights the Covenant before direct
perception and may declare the `LISTENING.md` revision that oriented its
hearing. Both paths produce the same listening-event shape.

The event separates covenant (what may happen), position (the listener's
relation to the object), apparatus (what could be sensed), apertures (what was
actually available), claims (what the evidence supports), and authority (what
may be done next). These boundaries survive the explicit Remember operation as
an Earworm auditum. Re-listening creates a new attributable record or revision;
it never silently overwrites the earlier hearing.

## Evidence and reasoning

The listening event is immutable evidence. A later conversation receives a
whitelist-built, covenant-filtered packet containing stable evidence
references. It can use deterministic local reasoning, a configured local
endpoint, an enabled host, or an enabled provider. A provider failure never
silently reroutes the packet to another service.

Model-backed interpretive listening and grounded conversation may also carry
the operator's bounded [`LISTENING.md`](listening-identity.md) perspective.
That document orients attention and voice; it is not part of the evidence
packet and cannot alter evidence, privacy, route, or Covenant policy.
The event stores only a content-free `oida/listening-identity/v0.1` reference:
revision hash, application state, and affected model roles. Private Earworm
context, shared akousma extensions, and conversation audits preserve that
reference without copying the identity text.

A reasoner may request one disclosed targeted re-listen when local audio is
available and policy permits it. That observation is appended as derived
evidence; it does not modify the original event.

## State ownership

| State | Default location | Owner |
| --- | --- | --- |
| Gateway and session state | platform OÍDA data directory | OÍDA daemon |
| Captured/uploaded audio | configured `OIDA_AUDIO_DIR` | OÍDA retention policy |
| Provider settings | OÍDA settings without secret values | OÍDA |
| Provider secrets | system keyring/Keychain or read-only environment | operator |
| Akousma records and objects | configured `AKOUSMATA_PATH` | Earworm store / Akousmata |
| Global listening identity (`LISTENING.md`) | OÍDA data directory | operator |
| Covenant documents | OÍDA data directory | operator |

Incognito listening disables durable conversation and memory writes and keeps
reasoning local. Remembering is always explicit.

## Surfaces

- Dashboard: `/`
- Remote capture UI: `/remote`
- Embedded Akousmata navigator: `/library/`
- Gateway discovery: `/gateway`
- REST index: `/api`
- Streamable HTTP MCP: `/mcp`
- Stdio MCP: `oida gateway --stdio --ensure-daemon`
- CLI and lifecycle: `oida`, `oida start`, `oida agent`, `oida doctor`
- Native macOS shell: `apps/macos`

The daemon binds to loopback by default. This repository serves the remote
page but does not publish or configure a machine-level network-access service.
Any non-loopback deployment must supply its own authenticated HTTPS boundary
and keep OÍDA's host/origin and bearer-token guards enabled.

## Extension points

- Add perception or reasoning providers behind the existing adapter
  interfaces; do not let adapters alter claim categories.
- Add host integrations under `integrations/`; pin the active Python runtime
  so adapters do not depend on shell state.
- Extend the gateway additively and publish the schema from
  `/gateway/schema/host-perception`.
- Extend the Akousmata store upstream in Earworm rather than duplicating store
  logic in OÍDA.
- Add UI state through daemon endpoints and the SSE stream so web, native,
  CLI, and MCP surfaces remain coherent.

## Validation

```bash
uv run pytest -q
python -m compileall -q oida harness bench_adapter scripts tests
node --check oida/static/app.js
scripts/run_local_checks.sh release
```
