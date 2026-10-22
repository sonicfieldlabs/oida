# Research context

These are local source additions, not a published wheel or an activated owner.
Oída keeps its existing local `SonicFieldBridge`, adds anonymous reads from the
fixed public Discovery MCP endpoint, and retains selected references in the
existing reasoning conversation. It does not create a second wiki, memory store,
scoring engine or canonical research scheduler.

## Public retrieval

The endpoint is `https://discovery.sonicfield.org/mcp`; callers cannot change it.
Only `search_sonic_field`, `get_sonic_field_record` and
`get_sonic_field_relations` are called. Discovery owns its public API adapters,
canonical links, upstream cache, per-family search and continuation semantics.
Oída accepts the published Discovery 1.1 result contract and the JSON or SSE
response format of that stateless MCP surface. It sends no authentication,
cookies, ambient proxy settings, private account state, event object or audio.
No external source URL or catalog tool is fetched or executed.

| Owner route | Input and effect |
| --- | --- |
| `GET /sonicfield/public/status` | Configuration and last request outcome; no upstream probe. |
| `POST /sonicfield/search` | Explicit public-safe literal query, selected families, locale, supported family filters, limit and optional returned cursor. |
| `POST /sonicfield/record` | Exact `family`, `slug`, optional `expectedId`, and locale. Use the selected result’s ID. |
| `POST /sonicfield/page` | Exact supported HTTPS Sonic Field `url`, optional expected ID and locale; root article URLs additionally require `journal` or `radar` family. |
| `POST /sonicfield/relations` | One bounded outgoing Lexicon `links-to` read. No automatic detail fetch or graph expansion. |
| `GET /sonicfield/schema/reference` | `oida/research-reference/v1` JSON Schema. |
| `GET /sonicfield/schema/context` | `oida/research-context/v1` JSON Schema. |

The POST routes also accept the existing optional `operation_id` and
`deadline_at`. The ordinary `/operations/{id}` and cancel route expose their
receipt when an ID was supplied. Reusing an ID refuses rather than dispatching
again. Cancellation uses the existing cooperative publication fence; a network
wait may last until the bounded transport settles, and late results are dropped.
A settled operation receipt does not mean that retrieval succeeded: inspect the
result’s `outcome` and Discovery `ok/error` branch.

Limits are five returned/selected sources per request, one data-tool call per
public request, three concurrent client calls, 512 KiB decompressed response and
25 seconds for the whole network operation. Hydrating up to five selected
records shares a 25-second deadline. A narrower enclosing owner deadline wins.
There are no client retries, automatic pagination, implicit relation hops or
owner disk cache. These limits implement a bounded primitive; composing more
calls requires an explicitly admitted recipe in a later phase. A transport
failure never switches to the local index, general web or another service.

Search supports all nine named families. Omitting families keeps Discovery’s
editorial default: Journal, Lexicon, Library and People. Ranking is within
retrieved candidates. Continue only with the returned cursor and unchanged
query/families/locale/filters/limit. `hasMore`, per-family matching/coverage,
partial failures, pagination and stop reasons remain in the response. An empty
bounded result is not evidence of worldwide absence. Invalid/stale cursor and
identity/schema mismatch are explicit failures. A relation target remains a
lead until its own detail is selected; an outgoing link proves no influence.

The original local `/sonicfield/explore`, `/sonicfield/status` and
`/sonicfield/reveal` routes retain their local behavior and request shapes.
Local index availability is separate from public last-request status. That
index preserves original case/diacritics in excerpts, normalizes Unicode for
matching, and uses the current Library item route. It still reports its legacy
local scope; it does not certify complete modern-site coverage or freshness.

## Reference and exposure contracts

`oida/research-reference/v1` contains:

- A `reference_ref` of `sfref:<snapshot_sha256>`, role `context` and origin
  `public_discovery`.
- The validated, bounded Discovery record: exact family/ID/slug/type,
  canonical and external source links, credits, text level/kind/source field,
  text and truncation/available-character information, requested locale,
  declared language/fallback state, rights and warnings.
- Source modification date plus API/canonical retrieval/validation times,
  etags and response metadata. A successful HTTP request is not a new source
  event, observation timestamp or permission grant.
- `excerpt_sha256` over exact returned UTF-8 text, without lowercasing or
  silently expanding a summary. `snapshot_sha256` hashes compact sorted UTF-8
  JSON of all other fields except `reference_ref` and `snapshot_sha256`.
- An ancestry/review block. Discovery supplies no independent-source proof;
  newly retrieved references therefore start `unknown` and `unreviewed`.
  Similar wording or a new host cannot invent derivation or independence.

A changed snapshot or unsupported wrapper revision fails validation; it never
falls back to legacy title/text. Fresh reads create their own snapshots, with
the source identity retained separately for comparison. Record-language
`unknown` stays unknown even when the transport language is `en`; requested
Spanish cannot establish original Spanish. Rights `unknown` is not replaced by
a catalog license tag, public availability or model confidence. Library
bibliographic text is not full-paper access. Source text is untrusted data.

`oida/research-context/v1` identifies the existing retained `subject_ref`, the
selected `reference_refs` and an `observe_only` action boundary. Selection
starts as `selected_context`. The committed turn separately records
`prepared_reference_refs` and `withheld_reference_refs` after whole-item prompt
budgeting; it becomes `informed_context` only when a reference fits in that
prepared input. Provider metadata, repair/fallback input and citation validation
use the same bounded item set. The turn audit distinguishes offered, supplied
and withheld evidence refs, so an omitted reference cannot be cited as if it
was supplied. This is preparation evidence, not proof of causal influence,
reading or understanding by a model. The context grants no
tool, model, source-use, retention, training or publication permission. It is
not a claim that an earlier independent acoustic account has been improved.

The model packet gets a bounded `reference` evidence item with
`ref: context:sfref:<snapshot_sha256>`, exact source identity/depth and relevant
limits. Full URLs and source metadata remain in the retained research object;
the existing packet’s path/URI filtering remains in force. A separate
`model_excerpt_sha256` describes the filtered model excerpt, while the original
excerpt and snapshot hashes still resolve to the selected source bytes.
Untrusted references cannot become acoustic `heard` or `measured` evidence,
adopt LISTENING.md, change a covenant or authorize execution. The identity file,
apparatus, source/reference role, applicable permissions and evidence stay
separate.

## Explicit selection in retained reasoning

First search or inspect an exact public page. To attach chosen records to an
existing retained account, use the existing `/reasoning/workspace/ask` route:

```json
{
  "request_id": "selected-context-example",
  "record_id": "akm_selected_existing_record",
  "question": "How does this concept frame the retained account?",
  "provider_id": "local_structured",
  "scope": {"memories": false, "wiki": false, "web": false, "sonicfield": true},
  "sonicfield_records": [
    {"family": "lexicon", "slug": "acoustemology", "expectedId": "acoustemology", "locale": "en"}
  ]
}
```

That example is a request shape, not a created record or model result. With
`local_structured`, the references are prepared and recorded as exposure, but the
deterministic reasoner answers from the account's own evidence and does not read
them; use a text model to reason with selected context. The
existing owner checks provider selection, current anchor/covenant, incognito,
queue, cancellation and deadline. Scope off rejects attached SF identities.
Scope on with no selected identities makes no public request. Hydration sends
only the exact selected public identity/locale; the record summary, listening
question and automatic private query are not sent to Discovery. Failed or
withheld references remain explicit retrieval notes, without fabricated source
content. Turning SF scope off excludes earlier SF-enriched dialogue from later
model history. A saved conversation retains the exact reference objects,
source-list hash, retrieval receipts and exposure in its existing `research`
field, leaving the canonical listening record unchanged.

The public MCP primitives are `oida_sonic_field_search`,
`oida_sonic_field_record` and `oida_sonic_field_relations`. They route through
this owner instead of constructing a second research store. Khipu forwards
the same primitives under `/api/owner/sonicfield`; it classifies public network
queries as work, preserving pause/stop and workspace admission. Its
source/initial/return inspector consumes the retained owner projections; see
the account and return section in [local research context](local-research-context.md).

## Compatibility, custody and later work

These are optional Oída application contracts, not new AKOÚŌ, Earworm or MASA
protocol versions. AKOUO package 0.12.1 declares `akouo/v0.10`; the existing
Oída `oida/gateway/v0.6` host input profile retains its `akouo/v0.9` declaration.
That is a named consumer profile, not evidence that all v0.10 fields are
implemented or that package and protocol numbers should match. Earworm’s
default constructor remains schema 1.6; separate opt-in 1.7–1.9 custody
contracts remain explicit. Historical records and frozen bytes are unchanged.

Canonical proposals/reviews remain owned by Akousmata through the existing
A11/Earworm validation and shared store. A free SF reference is not a canonical
acoustic record or a new A11 evidence source. Its owner-side promotion/export
mapping requires selected-use admission through that service; no parallel
truth graph or automatic canonical write is added here. The reference wrapper
is retained in reasoning custody, not inserted into old required record fields.

Selected local passage access uses separate optional contracts, the existing
material-use table and current-use checks. See [local research context](local-research-context.md)
for its explicit catalog, private selection, ancestry and withdrawal boundaries.
P5 adds the Khipu inspection/manual-return UI at source and fixture layers;
P6 adds [bounded selected recipes](research-recipes.md) on the existing queue;
P7 owns installed cohort qualification; P8/P9 own evaluation/training;
P10/P11 own website and release work. Current source tests or public retrieval
reads do not qualify those later outcomes. Application source changes are not
inside the already pinned companion wheels and do not restart the live daemon.

## Audit repair provenance and recovery

The 0.14.1 candidate retains `audit.research_evidence_shown`: the exact bounded
reference and re-listen values present in the prepared packet. Existing passage
retention and current-access checks govern the containing turn. It is not a
reconstruction from the full source snapshots.

`AnswerBlock.claim_scope` defaults to `acoustic`. An acoustic `fact` requires
cited measured evidence; model summaries and specialist interpretations cannot
alone establish it. An attributed statement about a source uses
`claim_scope: source_report`. This structural distinction does not verify the
truth of prose or replace listening and human review.

`POST /reasoning/workspace/jobs/{id}/abandon` resolves an uncertain submission
under the same lock as admission. It returns an existing job or durably closes
an unadmitted ID. A late `/ask` with that ID is refused; it is never replayed.
Only explicit new submissions receive a new identity. Legacy jobs remain stored;
queued legacy jobs receive a full-ID digest before execution, while ambiguous
running legacy identities require restart reconciliation.
