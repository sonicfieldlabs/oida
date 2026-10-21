# Selected local research context

Oída can consult explicit local passages as subject-bound research context. This
is an optional source implementation. It does not activate a running owner,
index a wiki, ingest books, create material grants or write a canonical account.
The public Discovery and existing Akousmata-generated wiki scopes remain
separate. Neither scope searches ALEPH or SonicBrain automatically.

## Catalog and selection

`OIDA_LOCAL_PASSAGES_MANIFEST` names an owner-maintained UTF-8 JSON catalog outside
the source repositories. With no manifest, local consultation is disabled. Its
contract is `oida/local-passage-catalog/v1`; the Pydantic `Catalog` and
`CatalogEntry` definitions in `oida/local_passages.py` are the schema authority.
The private phase packet supplies a metadata-only pilot catalog, never packaged
research text. Configuring a catalog does not authorize any material use.

The four corpus keys resolve under the current operator's home:

| Corpus | Read-only source root |
| --- | --- |
| `aleph` | `Documents/CHIXI/ALEPH/wiki` |
| `sonicbrain` | `Documents/SFL/sonicbrain` |
| `listeningmd` | `Documents/SFL/listeningmd` |
| `akdraft` | `Documents/SFL/akdraft` |

A catalog entry names an exact relative file, original SHA-256 and byte count,
one start-inclusive/end-exclusive UTF-8 byte interval, and its excerpt SHA-256.
It retains original language, creators, year, edition, complete line bounds,
printed page and physical sheet separately, or EPUB member/document/segments.
Extraction and mapping limitations remain explicit. An editorial paraphrase
has its own language field and is never a translated quotation. A hash binds
selected bytes; it does not certify OCR, pagination, attribution, factual truth
or human review. Metadata and hashes can themselves be private.

Sources are regular files, opened read-only through no-follow descriptors for
every directory and final component. Absolute paths, traversal, URLs, symlinks,
changed files and mismatched slices fail closed. Each source is at most 16 MiB;
each selected passage is at most 16 KiB; an inquiry selects at most five. The
catalog admits at most 128 selections and 512 KiB of metadata. Original files
are hashed in bounded chunks, and only the selected bytes are returned. There
is no hidden prefix, whole-file text cache, search index or automatic expansion.

## Current uses and ancestry

The catalog's `terms_ref` resolves through Earworm's existing `MaterialTerms`
table in the canonical shared store. Use the existing local-owner material
declaration endpoint, `PUT /representations/terms/{terms_ref}`, with explicit
actions, basis and expected current revision. This is an owner declaration of
operational uses, not proof of a license, community authority or another party's
acceptance. Do not reuse a term identity for different source bytes; the pilot
uses a source-hash-specific identity for each work's selected slices.

| Operation | Required current actions |
| --- | --- |
| Catalog metadata | `read_metadata` |
| Inspect selected text | `read`, `read_metadata` |
| Persistent subject-bound reasoning | `read`, `read_metadata`, `execute`, `retain`, `retain_metadata` |
| Supply context to an external or unknown provider | The applicable actions above, `disclose_external`, and this selection's explicit `allow_external: true` |

Selections bind the inspected terms revision. Revocation, expiry or replacement
invalidates the old selection; regranting does not silently revive it. Slices of
one work share a withdrawal scope. Declared parent works present in the catalog
also impose their current restrictions on derived projections. Missing ancestry
stays unknown; unresolved scholarly parents do not create permission or proof
of independence. Shared `source_group` and `derived_from` remain visible in the
reference and model packet. There is no citation-count corroboration engine or
automatic canonical promotion. Wider graph review remains an owner workflow.

Permissions are checked before and after reading source bytes, before provider
dispatch (including repair), at commit and on later conversation/job reads.
Existing conversations store the selected snapshot and derived response under
the same current-use boundary. Withheld conversations and jobs disappear from
lists; direct reads refuse without returning their content. A reader without
the compatible local catalog fails closed. No private context is replayed from
conversation history: every later inquiry needs a fresh selection. A missing
source is not reconstructed from retained text for a new consultation.

This is an operation boundary, not retroactive recall from a completed provider
request or cryptographic erasure of historical files. Revocation blocks later
use; deleting a retained conversation uses the existing deletion endpoint.
Source-wiki originals are never deleted or modified by this feature. Khipu's
inspector clears private client payloads on lease expiry, hidden/inactive panels
and workspace changes, and discards late responses. Owner responses use
`Cache-Control: no-store`; no browser cache or background worker is added.

## Owner routes and retained inquiry

All three local research routes require the existing local-admin boundary:
The selected-read request body is limited to 8 KiB before JSON validation.

| Route | Effect |
| --- | --- |
| `GET /research/local/status` | Configuration and bounds, without reading a corpus or probing a model. |
| `GET /research/local/catalog` | Only metadata currently permitted for inspection, with current terms revision. |
| `POST /research/local/read` | One exact selected passage; no provider, retention, translation or export. |

Copy a permitted catalog identity and bind the inspected hashes/revision:

```json
{
  "passage_id": "selected-passage-id",
  "expected_source_sha256": "<64 lowercase hex characters from catalog>",
  "expected_excerpt_sha256": "<64 lowercase hex characters from catalog>",
  "terms_revision": "<current inspected revision>",
  "allow_external": false
}
```

That is a shape example, not an executable grant. `oida/local-passage/v1` returns
the exact original excerpt and passage metadata, the selection and a
content-addressed `lpref:<snapshot_sha256>`. The snapshot digest covers compact
sorted UTF-8 JSON of every field except its two snapshot identity fields. It
contains no absolute source path. Unsupported or altered snapshots fail closed;
they cannot fall back to legacy wiki prose.

Attach up to five such selections through the existing
`POST /reasoning/workspace/ask`, using `local_passages: [...]` and explicit
`scope.local_passages: true`, an existing `record_id`, question and chosen
provider/model. Automatic selection, incognito retention and public web
searches from this private inquiry are refused. Public Sonic Field context can
still use explicitly selected public IDs; private passage bytes never become
Discovery queries. Provider selection remains the owner's responsibility.

Local deterministic reasoning and admitted loopback model endpoints can use a
local-only selection. Host CLIs, unknown endpoints and Ollama cloud models need
the explicit external boundary. A stored `local` label cannot turn a host CLI
or remote endpoint into local inference. Host-managed prepare/commit also needs
external disclosure because it returns a packet to a separate model host.

The existing conversation's `research.sources` contains the optional local
reference; its `research.local_exposure` uses `oida/local-context/v1` without
changing public `oida/research-context/v1`. `selected_context` becomes
`informed_context` only for whole reference items in the prepared packet.
Prepared and withheld refs partition the selection. This is delivery evidence,
not proof of causal influence, reading, understanding or a better listening.

The model cites `context:lpref:<sha256>`. Its excerpt retains original whitespace
with the existing path/URI redactions; a separate model-excerpt digest describes
that projection. Context budgeting withholds whole items. Provider metadata,
repairs, fallback and citation validation use the same prepared item set, so a
withheld item cannot be cited or leak through provider metadata. Original text,
editorial prose and acoustic evidence remain distinct.

Khipu forwards these owner primitives and selected inquiries through its private
local research routes; it does not read the source corpus. No private passage
MCP tool, public export, training adapter, identity adoption, tool executor or
autonomous recipe is added. Current-use grants cannot create a legal or cultural
authority. Canonical research/proposal/review stays with Akousmata. Activation
and release remain separate phases.

## Account and return inspection

`GET /reasoning/workspace/records/{identifier}` returns an optional
`oida/research-record/v1` projection after the current canonical account/policy
check. It contains the retained account digest, a bounded claim projection,
newest permitted conversations and bounded job identities/statuses. A missing
anchor is never reconstructed from an old conversation. The session read uses
`oida/research-session/v1`, preserves the conversation's initial account and
turn snapshots, and reports whether the current canonical anchor changed. A
turn digest identifies the complete retained turn, not just its UI projection.
Private retained responses remain under the original current-use gate.

An inquiry may add `comparison: {kind, reference, sha256, conversation_id?}`.
`initial_account` binds the current record ID and digest; `reasoning_turn` binds
an exact turn in the selected same-anchor conversation and requires that
conversation ID. Stale, cross-conversation or cross-anchor baselines fail before
queue admission and are rechecked during execution. The optional retained
`oida/research-comparison/v1` receipt records the display baseline and same
retained account relation. Earlier dialogue is omitted from provider input in
this mode, including re-listen context recompilation. A fresh private selection
is still required. Requests without a comparison retain their prior idempotency
shape and history behavior, including the private-history exclusions above.

Khipu's Stack → Reasoning view prepares explicit inquiries and its record
inspector compares retained initial/return statements, resolves each turn's
citations and displays original language, edition, locators, ancestry and
prepared/withheld exposure. A current-source read is separate from the retained
version. Exact text differences do not certify improvement or influence; these
returns do not themselves start a new acoustic pass. Canonical relations remain
an explicit read of the existing Akousmata owner. [Bounded recipes](research-recipes.md)
compose these selected reads and an optional separately selected local acoustic
return on the existing queue. Current private uses and fresh selection still
apply; public MCP wrappers do not admit private/audio recipes. Live selected
material/model demonstrations and installed cohort qualification remain later work.
