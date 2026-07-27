# LISTENING.md And The Oída Harness

Oída has a fixed safety/evidence harness and one explicit user-authored
listening identity. They are intentionally different layers.

`LISTENING.md` is the global, local Markdown document for the ear's situated
perspective: how it should attend, relate, speak, and ask. It begins as an
empty file, so installing or starting Oída does not silently impose a
personality.

It is closer to a declared listening position than to a fictional soul. A
personality describes how an agent appears; a listening identity also states
where attention begins, what relations it tries to sustain, which questions
it keeps open, and how it should meet its own limits. The file makes a
disposition inspectable and revisable instead of leaving it buried in a
provider prompt.

## Location And Editing

The canonical file is:

```text
<Oída data directory>/LISTENING.md
```

On macOS with the default configuration this is:

```text
~/Library/Application Support/oida/LISTENING.md
```

`OIDA_DATA_DIR` changes the parent directory. The shared web dashboard and the
native macOS app edit the same file under **Settings → Listening**. It can also
be edited with any UTF-8 text editor. Oída reads it again for each request, so
a daemon restart is not required.

The local API exposes the same document:

```http
GET /listening
PUT /listening
Content-Type: application/json

{"text":"Listen as a careful guest."}
```

Connected host agents use `oida_listening_identity(action="read")` before
direct host perception and may use `action="set"` only when the operator
explicitly asks to change the file. `oida_capabilities` also reports the
active state and revision, without the text or local path. Reading the document
is therefore an explicit tool action.

The active harness uses at most 4,000 characters. The endpoint writes
atomically, and reports the exact path, active state, character count, and
content digest. New files are private to the local user. Manual edits are
bounded on read, so an accidental large file cannot consume a model context.
Dashboard saves include the digest they loaded and return a conflict instead
of overwriting a newer edit. API and MCP clients can supply the same
`expected_sha256` value.

## A Freeform Document With An Optional Grammar

Oída does not parse Markdown headings into policy. The document remains
freeform, but the following scaffold makes its possibilities legible:

```markdown
# Listening identity

## Position
From where, with whom, or on whose behalf does this ear listen?

## Conditions
Which material, social, ecological, historical, or technical conditions
should remain present to attention?

## Intentions
What relation should this listening attempt: care, diagnosis, accompaniment,
comparison, estrangement, study, composition?

## Fields of attention
Which supported sonic dimensions should be foregrounded when evidence exists?

## Context and relations
Which lineages, places, bodies, media, or communities should not be flattened?

## Questions to keep open
What should remain unresolved rather than forced into identification?

## Voice and possibilities
How should Oída speak, ask, compare, or propose a next listening?
```

These headings are prompts for reflection, not schema fields. A declared
condition is not an apparatus measurement, and context written here is not
evidence about the current sound. For example, “listen for extractive
infrastructure” can direct attention toward supported traces; it cannot prove
that infrastructure is present. Technical bandwidth and blind spots still
come from the apparatus record. Historical context still needs an attributable
source. A possibility remains a proposal, not a finding.

## What It Can And Cannot Do

`LISTENING.md` may orient:

- perceptual attention in interpretive model passes;
- relational stance toward a sound, place, work, or listener;
- conversational tone and the kinds of grounded questions Oída asks;
- which supported dimensions are foregrounded when evidence is available.

It cannot:

- create an observation or turn a preference into evidence;
- change confidence or the AKOÚŌ evidence category;
- reconstruct material withheld by privacy or a Covenant;
- replace an AKOÚŌ route, output schema, or explicit task;
- stylize exact transcription. DSP and literal transcription do not consume
  the identity document.

For model-backed perceptual work, the identity is appended to the bounded
task with an explicit instruction that the task, format, evidence,
uncertainty, privacy, and Covenant remain authoritative. A daemon-managed
multi-pass listen uses one snapshot of the document so its passes do not
acquire different identities halfway through an event.

Every normalized listening event carries a content-free
`oida/listening-identity/v0.1` provenance block. It records the bounded
document's SHA-256 revision, whether it was active, where it was applied, and
whether a manual edit was truncated. It never stores the identity text. The
same reference travels into private Earworm context and, when remembered, the
akousma's `extensions["oida.listening_identity"]`. A conversation turn records
the revision that shaped its prompt separately from the identity that shaped
the original hearing. This lets two hearings be compared as hearings made
from different positions without treating either position as sonic evidence.

For grounded conversation, prompt precedence is:

1. Oída's non-negotiable evidence, privacy, Covenant, and output rules.
2. Trusted AKOÚŌ route/task guidance.
3. The global `LISTENING.md` perspective.
4. The structured conversation profile.
5. Optional profile-specific instructions.
6. The question, dialogue history, and evidence packet as bounded input.

No lower layer can override a higher one. The fixed hard rules remain in
`oida/reasoning/prompts.py`; they are application policy rather than a user
personality file.

## Listening Identity Versus Covenant

| Layer | Question it answers | Runtime force |
| --- | --- | --- |
| `LISTENING.md` | “From what perspective should this ear attend and respond?” | Bounded orientation only |
| Conversation profile | “How should this particular dialogue be presented?” | Per-profile style and focus |
| Listening Covenant | “What may this ear listen to, retain, or reveal?” | Enforced gates and redaction |
| Hard harness | “What must every provider preserve?” | Non-negotiable evidence, privacy, and schema policy |

A poetic or ecological identity therefore never substitutes for a Covenant.
Conversely, a Covenant's commitments are carried as governance context but do
not become a hidden personality prompt.

The order matters for hosted perception too. A host should inspect the active
Covenant before it gives audio to its own model, then read `LISTENING.md`. If
the host cannot enforce an input refusal before perception, it must disclose
that limit and prefer Oída-owned local perception when a path is available.
Identity never repairs or relaxes that limitation.

## Hosted Ears And Revision Handshake

The host-perception contract `oida/host-perception/v0.4` accepts an optional
declaration alongside the host's observations:

```json
{
  "listening_identity": {
    "contract": "oida/listening-identity/v0.1",
    "sha256": "<digest returned by oida_listening_identity>",
    "applied": true
  }
}
```

When the digest matches the daemon's current bounded revision, the event marks
the application as `host_declared`. This is honest provenance, not proof of a
model's internal attention. A missing declaration becomes
`available_not_declared`; a changed revision becomes `revision_mismatch`.
Neither state changes claim categories. This small handshake prevents Oída
from silently attributing its own identity to a hearing made elsewhere.

## Provider Boundary

The document stays local when the selected model path is local. Like profile
instructions, it accompanies a request sent to an explicitly enabled external
conversation or audio provider. Enabling such a provider is therefore also a
decision to share the active `LISTENING.md` text with it; raw-audio and other
sharing gates remain separate. Events, memories, exports, and conversation
audit records carry only the content-free revision block unless an operator
chooses to share the source file separately.
