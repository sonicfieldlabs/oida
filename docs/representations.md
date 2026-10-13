# Primary inscriptions and textual execution

The unpublished Oída 0.13.1 candidate admits text as primary listening material
and can finish a sounding operation as a complete text work. Accounts and works
retain separate identities, authorship and source relations. The owner chooses
a text-capable provider and model explicitly. This path calls the existing
provider transport without an audio engine, microphone, STT, TTS or GERM render.
It does not establish what the model experienced.

## Admitted material

| Form | Declared project convention | Actual supplied view |
| --- | --- | --- |
| Text/Markdown | `oida/plain-text/v1` | Exact UTF-8 bytes and layout |
| Structured data | `oida/json/v1` | Original JSON text after strict validation |
| Morse | `oida/morse-latin/v1` | Separately identified A-Z/0-9 decode; original dot/dash/slash inscription retained |
| Notation | `oida/notation-text/v1` | Original textual notation; no inferred notation system |
| Glyphs | `oida/glyph-inscription/v1` | Original Unicode inscription; no universal decoding claim |
| Code | `oida/code-inscription/v1` | Original text; code is never evaluated |
| MIDI | `oida/midi-events/v1` | Separately identified Standard MIDI File event projection in authored ticks |

MIDI admission supports bounded format 0/1 files with PPQ timing. The parser
retains the binary source, identifies each event by track and tick, and records
projection losses. SMPTE division, format 2, unknown conventions and invalid
registries stop before provider execution. Tick positions are not acoustic
onsets or a claim that a synthesizer played the notes. The file format reference
is the [MIDI Association's Standard MIDI Files](https://midi.org/standard-midi-files).

Sources are limited to one MiB. Complete submitted prompts are limited to
131,072 UTF-8 bytes. An explicit source excerpt has its own received identity,
exact byte ranges and omissions; it never changes the full source hash. The
file reader accepts only paths inside explicitly supplied roots. Invalid UTF-8,
NUL text, duplicate JSON keys and non-finite JSON numbers are rejected.
JSON numbers are validated as exact decimals, including finite values too large
or small for binary floating point. An exponent outside the decimal parser's
supported range is refused. The parsed values serve admission only; the provider
receives the original JSON text, including its spelling and layout.

## Authorship and adoption

`ListeningProfiles` stores editable drafts, immutable complete revisions,
compiled profiles, owner authority declarations and scoped lifecycle state in
an owner database. Saving or freezing a draft does not adopt it. Importing any
of the five canonical examples does not adopt it. Adoption, replacement and
revocation require current owner authority and an expected lifecycle generation.
A workspace scope must identify the authenticated owner workspace.

The HTTP host currently admits workspace and optional workflow scopes, with
the authenticated owner as its sole declared party. Run/swarm scopes need a
coordinator adapter with actual identities, lifetime and participants. Caller
labels do not supply those facts. Invocation identities belong to the executing
owner. The underlying lifecycle contracts retain their broader scope grammar.

The new versioned path is separate from the historical
`oida/listening-identity/v0.1` orientation interface and its 4,000-character
limit. Existing audio callers keep that interface. A historical Covenant that
closes file admission or retention still closes the corresponding action here;
an unsupported historical rule blocks the new path pending a qualified adapter.

Each applicable input, execution, output, retention and disclosure rule reads an
actual owner fact. Attempt counts belong to the declared run, or the workspace
when there is no run scope. Required unknown controls block adoption. Prose
commitments remain attributable without becoming enforcement claims. Replacement,
revocation, expiry and changed parent bindings are checked at subsequent gates.

Adopted document context is complete unless its profile permits an explicit
selection. The selected bytes, full document hash, metadata hash, binding and
actual received artifact remain separate. Documents exceeding their byte budget
fail without truncation. A declared model token window also requires a qualified
counter with a method reference and a 2,048-token output reserve. The generic API
does not invent a tokenizer: models with a declared window stop when that counter
is absent. With an undeclared window, the receipt marks token limits unknown.
Submitted bytes and a provider result do not prove attention or complete reading
inside the provider.

## Owner interface

Record and Station lists add `next_cursor` to their existing contracts. Continue
even an empty page when it is non-null. The opaque token binds actor, audience
and filters, expires after 15 minutes, and reveals no denied identities or
counts. Each indexed query scans at most 512 candidates and returns at most
200 permitted records. `query` searches permitted record metadata; `form` and
`output_kind` filter the same traversal. Station checks local disclosure during
traversal, rather than filtering only the first owner page. The MCP memory tool
accepts and returns this continuation without collecting hidden totals.

An optional `expected_profile_generation` pins inspected adoption state.
Oída checks it within the transaction that resolves adopted context. A change
refuses execution before the provider call. Each `context_inputs` entry can
add an explicit `selection` of ordered start/end UTF-8 byte ranges beside its
retained `representation_ref` and `role`. `context_selections` continues to
identify adopted-file ranges by adoption reference. Received partial artifacts
retain the existing access and transformation contracts; none replace original
bytes or activate a source as instructions.

All new Oída endpoints require the existing local administrator boundary.
Workspace launches also retain the existing generation and store binding checks
for mutations. Capabilities can be read without opening an owner database.

| Endpoint | Responsibility |
| --- | --- |
| `GET /representations/capabilities` | Explicit admission and budget limits |
| `PUT /listening/profiles/drafts` | Save complete draft with expected SHA-256 |
| `POST /listening/profiles/revisions` | Freeze revision; no adoption |
| `GET/POST /listening/profiles/presets[/name]` | Inspect/import canonical examples |
| `POST /listening/profiles/authorities` | Explicit owner scope/party/action declaration |
| `DELETE /listening/profiles/authorities/{ref}` | Revoke that authority |
| `POST /listening/profiles/adoptions` | Adopt or atomically replace at a scope |
| `POST /listening/profiles/adoptions/{ref}/revoke` | Revoke an adoption |
| `PUT /representations/terms/{ref}` | Declare current material uses with revision and optional UTC expiry |
| `POST /representations/execute` | Read, sound or return through the selected provider |
| `GET /representations/records[/{ref}]` | Query/retrieve retained sources and outputs |
| `GET /representations/executions/{ref}` | Read permitted execution metadata |

An execution supplies exactly one new base64-encoded artifact or retained
`source_ref`. It names `intent`, `provider_id`, `model_id` and an enclosing
authenticated scope. `remember` defaults to false. Additional retained material
must declare its `account` or `context` role. A return names the exact previous
operation, original/descendant relation and independent/informed/shared/unknown
context basis. The independent case cannot include a prior account.

The MCP tools `oida_inscription` and `oida_representation_memory` expose the
versioned path. They cannot grant terms or adopt profiles. New material needs
owner-declared current terms before execution. Missing permission stops the
action; it does not prompt an agent to create a grant from the source text.

## Completion and custody

The result distinguishes a completed account, a completed work, and an explicit
non-response. An empty finished inscription is different from non-response.
Accounts may use measured, inferred, interpreted, speculative or undetermined
claims. Measured claims resolve to the supplied deterministic byte measurement;
claims of cochlear hearing are outside this route. Structural validation does
not verify the truth of every sentence. A completed work remains composition.
If a profile requires an actual output form, non-response supplies no such fact
and cannot clear that required output gate. A profile without that requirement
can retain the explicit non-response as its own outcome.

Retained artifacts enter the canonical shared Earworm store through Akousma
1.9 admission. Exact bytes, metadata and record membership commit together.
Current material uses are declared in the same shared store and intersect across
every generated or transformed input. Operational owner permission does not
establish a license, another party's consent or legal standing. Read, retention,
provider disclosure, owner disclosure and export remain separate uses.

Execution receipts identify selected and actual model, exact prompt/request
digests, received artifact hashes and sizes, applied profiles, token status,
provider call and persistence outcome. Synthetic providers are labelled. Without
retention, the profile ledger keeps only permitted metadata and a claim-report
digest, never the source, work or claim prose. Receipt access rechecks current
metadata permissions. Provider failure has no automatic model fallback.
An adopted execution requires permission to retain its application metadata;
without that permission it stops before a gate writes a material-linked receipt.

Khipu's local candidate integrates source selection, explicit owner tokenizer
wiring, editing/adoption, History and Station readers. Phase 10 reconciled and
checked the candidate owner cohort in an isolated offline installation. The
retained Phase 6 text trial used a selected local Qwen3.5-4B deployment under the
authenticated local operator. Its result remains evidence for that deployment
and encounter. The other software examples use synthetic providers. These checks
did not replace an active owner installation or configure an active Eco host;
public release and broader live-provider qualification remain pending.
