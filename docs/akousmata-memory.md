# Listening Memory

Oída uses three distinct memory layers. Keeping them separate prevents session
context, compatibility data, and the shared Listening Stack library from being
mistaken for one hidden archive.

1. **Earworm session context** relates events during an active workflow. It is
   not durable sonic memory by itself.
2. **Oída compatibility traces** support the `/memory` API and CLI. They store
   selected listening events as inspectable JSON under Oída's application-data
   directory.
3. **The shared akousma store** is the canonical Earworm/Akousmata library used
   by the Memory navigator and GERM handoff. It stores portable records and
   lineage across applications.

No layer creates a durable listening silently. A user must choose Remember, a
request must explicitly ask to remember, or a configured background action
must carry that instruction.

## Storage

Oída compatibility traces live under the platform application-data directory:

```text
<OIDA_DATA_DIR>/akousmata/traces/
```

On macOS, `OIDA_DATA_DIR` defaults to
`~/Library/Application Support/oida`. On other supported systems it follows
`$XDG_DATA_HOME/oida`. The shared akousma store is selected through
`AKOUSMATA_PATH`; if unset, the `akousma` package uses its platform-local
default. These locations are runtime data, never repository content.

## Compatibility Trace Contents

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

Compatibility traces exist for the Oída API; they are not a competing
portable record format.

## Raw Audio Policy

Every trace declares how its audio was handled:

- `external_ref`: a local file is referenced; raw audio is not copied into
  memory.
- `temp`: the trace came from a temporary buffer; memory stores derived data
  only.
- `saved`: raw audio was explicitly saved.
- `not_stored`: no raw audio reference is kept.

The dashboard and `/memory` API surface this policy. Forgetting a trace removes
the derived record, not a source file that was only referenced.

## Retrieval

Browse or search compatibility traces:

```bash
curl -sS "http://127.0.0.1:8765/memory?q=machine+hum"
uv run oida memory search "machine hum"
```

Supported filters on `/memory` and `/memory/export` are `q`, `tag`,
`source_kind`, `route`, `since`, `until`, and `limit`. Feature similarity uses
deterministic DSP when embeddings are unavailable. It is a pragmatic signal
comparison, not a learned semantic judgment.

```bash
curl -sS http://127.0.0.1:8765/memory/trace/<trace_id>
curl -sS http://127.0.0.1:8765/memory/export
uv run oida memory export
uv run oida memory forget <trace_id>
```

## Dashboard and Shared Store

The dashboard provides Remember, Forget, search, open, and JSON export actions
for Oída traces. Opening a trace rehydrates its listening event in the result
panel.

The shared Memory rail reads the canonical akousma store. A Remember action can
file the same listening there so Akousmata can navigate it and GERM can create
a lineage-bearing descendant. Rename and forget operations on that record
never delete referenced source audio.

## Export, Retention, and Forgetting

- `/memory/export` exports Oída compatibility traces as JSON.
- Akousmata provides sanitized, manifest-bearing exports from the shared store.
- Incognito listening never enters durable history or either memory store.
- A listening covenant can restrict retention or withhold fields before a
  record is written.
- Forgetting can preserve an honest absence in relationships when the portable
  store needs to retain graph integrity.

See the [Earworm repository](https://github.com/sonicfieldlabs/earworm) for the
akousma specification and the
[Akousmata repository](https://github.com/sonicfieldlabs/akousmata) for the
reference navigator.
