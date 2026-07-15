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
                OÍDA gateway v0.2
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
              immutable listening event
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
| `akouo-contract` | Skills, commands, presets, schemas, evidence ladder, covenants, and routing vocabulary. | Audio decoding, model inference, persistence, or UI state. |
| `akousma` (Earworm) | Open sonic-memory record, lineage, kinship, provenance, and store operations. | Listening policy or application UI. |
| `akousmata` | Library, graph, map, timeline, wiki, research, consent/export, and human annotation over the store. | Audio perception or another producer's listening block. |
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

`POST /gateway/harness` accepts a declared observation from an
audio-capable host. Host observations remain model evidence; they are never
relabeled as measured DSP. Both paths produce the same listening-event shape.

## Evidence and reasoning

The listening event is immutable evidence. A later conversation receives a
whitelist-built, covenant-filtered packet containing stable evidence
references. It can use deterministic local reasoning, a configured local
endpoint, an enabled host, or an enabled provider. A provider failure never
silently reroutes the packet to another service.

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
