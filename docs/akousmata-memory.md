# Akousmata Memory

Akousmata is `hmm`'s local listening-memory layer. It stores selected listening
events as inspectable JSON traces. It does not create hidden session history:
memory writes happen only when the user explicitly remembers an event, or when a
background capture request explicitly asks to remember.

## Storage

The default store is local JSON under:

```text
akousmata/traces/
```

Each trace contains:

- `schemaVersion`
- `listeningEventId`
- `title`
- `sourceKind` and `sourceLabel`
- `audioPolicy`
- `features` and `similarityVector`
- route summaries
- tags and user notes
- memory links
- the normalized listening event JSON

## Raw Audio Policy

Akousmata records the raw audio policy for every trace:

- `external_ref`: a local file path is referenced; raw audio is not copied into
  memory.
- `temp`: the trace came from a temporary buffer; memory stores derived data only.
- `saved`: raw audio was explicitly saved.
- `not_stored`: no raw audio reference is kept.

The dashboard and `/memory` API surface this policy so saved traces stay
inspectable.

## Retrieval

Browse or search traces:

```bash
curl -sS "http://127.0.0.1:8765/memory?q=machine+hum"
uv run hmm memory search "machine hum"
```

Supported filters on `/memory` and `/memory/export`:

- `q`
- `tag`
- `source_kind`
- `route`
- `since`
- `until`
- `limit`

Feature similarity uses deterministic DSP features when embeddings are not
available. It is a pragmatic local comparison, not a learned semantic embedding.

```bash
curl -sS http://127.0.0.1:8765/memory/trace/<trace_id>
curl -sS http://127.0.0.1:8765/memory/export
uv run hmm memory export
uv run hmm memory forget <trace_id>
```

## Dashboard

The dashboard includes:

- `Remember this sound` on a current listening event.
- `Forget saved trace` for the latest saved trace.
- Searchable memory browser.
- Per-trace open and forget controls.
- Export JSON control.

Opening a trace rehydrates its stored listening event into the result panel.
