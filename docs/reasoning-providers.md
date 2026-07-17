# Reasoning Providers And Boundaries

Oída separates hearing from talking about what was heard. Perception produces
an accountable listening event. The reasoning layer receives a bounded view of
that event, answers questions, and can request one focused local re-listen. It
cannot edit the event or promote an interpretation into a measurement.

This boundary is the same for every provider. A local model, a host CLI, and an
external API receive the prompt and evidence that Oída composes; they do not own
the conversation policy.

## What Oída Owns

Oída owns seven pieces of every grounded turn:

1. The global [`LISTENING.md`](listening-identity.md) perspective: an optional,
   local, operator-authored orientation for attention and voice.
2. The conversation profile: tone, depth, initiative, focus, language, and up
   to 4,000 characters of custom instructions.
3. Prompt composition and precedence.
4. The covenant-filtered evidence packet.
5. Provider and privacy policy, including whether any external call is allowed.
6. The structured response contract and evidence-reference validation.
7. Audio-role routing, including model capability checks, resource warnings,
   and the separate policy gate for sending audio to an external provider.

The prompt is assembled in this order:

1. Non-negotiable evidence, covenant, privacy, and output rules.
2. Trusted route or task instructions.
3. The global `LISTENING.md` perspective as a bounded preference.
4. Structured conversation-profile settings.
5. Profile-specific instructions as bounded preferences.
6. The question, prior dialogue, and evidence packet as untrusted input.

A lower layer cannot override a higher one. In particular, `LISTENING.md` and
custom instructions can change voice or depth but cannot reveal withheld
material, include raw
audio, alter a listening result, or relax the response schema. Text found in a
transcript, filename, tag, memory, or earlier model output is data rather than
an instruction.

The system prompt is operating for every model-backed conversation, whether
the selected reasoner is a daemon adapter or an already active host using the
prepare/commit handoff. It is provider-independent and built in Oída's core.
The deterministic fallback does not call an LLM, but enforces the same evidence
and privacy policy procedurally. Audio perception calls use a smaller separate
hardening prompt that treats audible speech/lyrics as data rather than
instructions; it does not replace the conversation prompt. Interpretive
perception tasks also receive the bounded `LISTENING.md` perspective. DSP and
exact transcription remain outside that perspective layer so measurements and
quoted speech stay literal.

The identity is snapshotted once per turn. The conversation audit stores its
content-free revision block beside the prompt hash and profile id, so the
position shaping a later discussion remains distinguishable from the position
that shaped the original listening event. The local deterministic responder
records an active identity as available but not applied because it does not
consume a generative prompt.

Trusted AKOÚŌ route guidance is reconstructed from Oída's installed route and
command manifests. Event prose is never promoted into the system layer. A
Field, Voice, Music, Signal, or other route can therefore orient the dialogue
without changing the evidence ladder or manufacturing observations.

## Six Model Roles

The Reasoning settings in the shared web dashboard assign a provider and model
to each role. The native macOS shell embeds this dashboard instead of carrying
a second provider configuration.

| Role | Default | Purpose |
| --- | --- | --- |
| Fast perception | Oída MOSS Instruct | The ordinary local audio pass. |
| Deep perception | Oída MOSS Thinking | A more detailed local listening pass. |
| Transcription | Oída MOSS Instruct | Speech-to-text; may be reassigned to MOSS Transcribe + Diarize or another compatible model. |
| Music analysis | Oída MOSS Thinking | Music structure, instrumentation, production, and deeper musical analysis. |
| Conversation | Local structured | Grounded dialogue and deterministic fallback. |
| Targeted re-listening | Oída MOSS Thinking | One focused local pass requested during a turn. |

Assignments do not install a model. Oída uses models and endpoints already
present on the computer and never silently downloads weights or asks Ollama to
pull a model.

Model compatibility is validated per role. Targeted re-listening is always
local. Other audio roles may use a cloud model only when the provider is
enabled and the separate external-audio permission is on.

## Audio Model Catalog And Runtime Status

The catalog separates support from installation. `supported_local_host` means
Oída knows the role and request transport but expects the operator to run a
compatible loopback endpoint. `supported_untested_large` means configuration is
implemented but the checkpoint is intentionally not run on the current
machine. A catalog row never proves that weights, licenses, or runtime
dependencies are present.

| Family | Oída execution path | Important boundary |
| --- | --- | --- |
| MOSS-Audio 4B/8B Instruct + Thinking | Embedded MPS runtime or user-managed SGLang | 8B checkpoints are RAM-heavy; Hub lookup remains explicit-only. |
| MOSS-Music 8B Instruct + Thinking | Experimental embedded MPS or SGLang | The upstream project recommends SGLang for best quality. |
| MOSS-Transcribe-Diarize 0.9B | Local `/audio/transcriptions` endpoint | Dedicated multilingual timestamps/diarization role; not a conversation reasoner. |
| MiDashengLM 7B FP32 / 0.6B FP32 | Local OpenAI-compatible audio host | The requested 7B FP32 repository is about 33 GB; newer BF16 checkpoints are more practical. |
| MiMo-Audio | Compatible Linux/CUDA local host | Official runtime needs CUDA 12+, FlashAttention, Python 3.12, and the separate tokenizer. Base/tokenizer entries are visible dependencies, not assignable production roles. |
| Qwen3-Omni 30B-A3B Instruct / Thinking | Local CUDA/vLLM-style audio host | Configuration-only on this machine; Oída does not attempt to load or test these large checkpoints. |
| Gemma 3n E2B/E4B IT | Compatible local audio host | Hugging Face access is gated by Gemma terms; actual memory depends on precision/offload. |
| Mellow 167M | Custom local bridge | Experimental limited audio-language model, useful for research but not a general text reasoner. |
| Gemini 3.5 Flash | Google Generative Language API | BYOK; audio requires the external-audio opt-in. |
| Qwen3.5-Omni Plus/Flash and Qwen3-Omni Flash | Alibaba Model Studio OpenAI-compatible API | Hosted model ids are not aliases for the open Qwen3-Omni 30B weights; streaming output is aggregated. |
| Nemotron 3 Nano Omni 30B-A3B Reasoning | NVIDIA NIM API or OpenRouter free route | API support is configuration-tested, not exercised here. NVIDIA inputs above its inline limit use a temporary NVCF asset that is deleted after the request. The free OpenRouter prototype route is unsuitable for confidential recordings or personal voices. |

OpenRouter remains model-id configurable, so a future listed Qwen Omni audio
route can be selected without new adapter code. Oída does not currently invent
or pin an OpenRouter Qwen3-Omni id that the provider has not published.

Thinking-token budgets are backend capabilities, not generic prompt hints. The
embedded Transformers/MPS runtime does not implement the upstream sampling
processor and rejects budgeted requests. The `cuda-server` profile supports a
budget only when `OIDA_SGLANG_THINKING_PROCESSOR` contains the serialized
`Qwen3InstructionInjectionThinkingBudgetLogitProcessor`. A generic Local audio
host can use the same SGLang mechanism by saving that serialized value in its
advanced Connection setting. Oída sends both `custom_logit_processor` and
`custom_params.thinking_budget`; if the processor is absent, it refuses to
claim the budget was enforced.

The settings response includes a local resource assessment: physical RAM,
single-versus-multi residency, estimated peak model RAM, platform/runtime
incompatibilities, and warnings for unverified targets. Estimates are planning
guardrails rather than guarantees; quantization, KV cache, audio duration,
endpoint residency, and runtime overhead all affect real use. Oída never loads
a model merely to calculate this panel.

## Provider Choices

Every provider except `local_structured` and Oída's local MOSS catalog is
disabled by default. Enabling one is an operator decision. A successful probe
means that the adapter can reach the executable or endpoint; it does not change
the sharing permissions for transcript or memory content.

| Provider | Connection | Locality and credential notes |
| --- | --- | --- |
| Local structured | Built into Oída | Deterministic, local, no model and no network. It is also the visible fallback. |
| Oída MOSS | Configured local MOSS engine | Audio perception and targeted re-listening only. MOSS is not the general conversation provider. |
| Ollama | Existing loopback Ollama API | Local by default. Models are listed from the running service; none are pulled. |
| OpenAI-compatible | Operator-supplied base URL | Supports a loopback HTTP server or an explicitly trusted HTTPS endpoint. Plaintext HTTP is rejected off loopback. |
| OpenRouter | OpenRouter API | External. Accepts an API key or the localhost PKCE authorization flow. |
| Local audio host | Operator-managed loopback OpenAI-compatible audio/transcription API | Local model families beyond embedded MOSS. The endpoint must remain loopback for local-only targeted re-listening. |
| Google | Native Generative Language API | Gemini text/audio through BYOK. |
| Alibaba | Model Studio OpenAI-compatible API | Qwen Omni text/audio through BYOK; supported streaming responses are collapsed to final text. Set the endpoint shown for the API key's region/workspace when it differs from the preset. |
| NVIDIA | NIM OpenAI-compatible API | Nemotron Omni text/audio through BYOK. |
| Codex | Installed Codex CLI/app server | Uses the current Codex login in an ephemeral, empty-workspace, read-only turn with approvals denied, MCP cleared, web search disabled, and sandbox network disabled. The current app-server protocol does not expose a literal empty built-in-tool list. |
| Claude | Installed Claude CLI | Uses the current host login, JSON schema output, no session persistence, safe mode, and no tools. |
| Hermes | Installed Hermes CLI | Uses host credentials with an explicitly selected model for one safe-mode turn; Oída supplies the full envelope and applies a no-tool compatibility sentinel inside a temporary `HERMES_HOME` that is deleted on success, failure, or timeout. |
| OpenClaw | `openclaw infer` | Uses lean one-shot inference without an agent or tool context. The selected upstream model may still be remote. |
| OpenCode | Attached loopback or Oída-managed server | Creates a temporary session, applies Oída's system prompt, disables tools, selects the requested model, then deletes the session. Every managed server gets a fresh environment-only Basic Auth secret. |

Host authentication is convenient, but it does not prove that the selected
model is local. Codex, Claude, Hermes, OpenClaw, and OpenCode therefore have
`unknown` locality until the adapter can establish more. Oída requires explicit
enablement before using any of them.

OpenCode appears in both directions. It can be an outbound reasoning provider,
and `oida integrate opencode` installs the Oída MCP/skill integration so an
active OpenCode session can listen or complete a prepared turn. OpenClaw gains
the corresponding local integration through `oida integrate openclaw`.

## Evidence And Privacy

The conversation provider never receives the event object wholesale. Oída builds an
`oida/evidence-packet/v0.1` packet from an allowlist:

- typed claims and explicitly shared aggregate/route prose;
- AKOÚŌ claims with their category, source, basis, confidence, and time
  range when present;
- bounded deterministic DSP features;
- explicit uncertainty;
- an allowed subset of the active covenant record;
- transcript or memory evidence only when each permission is enabled.

Raw audio is never included in a conversation/evidence packet. Local paths, source URIs,
artifacts, raw reports, credentials, notes, and arbitrary nested event fields
are not traversed into it. Covenant withholding is applied before the packet is
compiled. Missing material remains missing; a reasoner cannot reconstruct it
from context.

Transcript sharing and memory-content sharing are independent and off by
default. Untyped legacy summaries, route prose, captions, and prior dialogue
are treated conservatively because they can contain spoken words without a
transcript marker. These switches govern packet content, not raw-audio access. A
conversation can include up to three comparison events, but the user must add
them explicitly. They remain context; none can replace the primary event that
anchors the conversation.

Incognito is stricter than the saved settings. It forces the deterministic
local conversation provider, disables external calls, and keeps the
conversation out of persistent storage.

External audio perception is a different, stronger permission. It is off by
default and is allowed only when all of these conditions hold:

- an audio-capable provider and model are assigned to the current audio role;
- that provider is explicitly enabled and has a credential;
- **External audio models** is explicitly enabled;
- the listen is not incognito; and
- the active or pinned listening covenant does not withhold raw audio.

Oída sends audio bytes, never a local path. Providers normally receive a
Base64 input; NVIDIA inputs above its small inline limit are staged as a
temporary authenticated NVCF asset, referenced for one request, and deleted in
a `finally` cleanup. A blocked or failed external route falls back to the
configured embedded local ear. Conversation providers still receive only the
filtered evidence packet. Targeted re-listening is always local, even if the
conversation reasoner is hosted or cloud-based.

## Response Contract

Model-backed providers return `oida/reasoning-response/v0.1` JSON with:

- answer blocks and their exact evidence refs;
- hypotheses with confidence and evidence refs;
- uncertainty notes;
- optional suggested questions;
- at most one `targeted_relisten` request when that action is allowed.

Oída validates the schema and every ref. The contract asks for conclusions,
citations, and uncertainty, never private chain-of-thought. Prompt text and
reasoning traces are not written into the conversation audit record.

If a provider returns malformed or uncited output, Oída makes one repair
request to that same provider. If the provider remains invalid, is unavailable,
or fails, Oída returns a visibly marked deterministic local response. It does
not silently retry the evidence packet with another external provider.

## Targeted Local Re-listening

A conversation model can ask Oída to listen again to a specific question or
time range. Oída accepts at most one automatic request per turn and only when:

- targeted re-listening is enabled for the turn;
- the original audio is still available as a local file;
- the active covenant permits the requested analysis;
- the assigned local audio model is available.

The pass runs through the local MOSS/audio path. Oída records the new
observation as separate `relisten` evidence, discloses that it ran, and gives
the conversation reasoner one final pass over the expanded packet. The
original event and its earlier claims stay untouched. A cloud or host reasoner
can request this action, but raw audio still stays local.

## Credentials

Non-secret provider configuration is stored in
`settings/reasoning.json` under Oída's data directory. It may contain an opaque
credential name, never the credential value.

On macOS, Oída writes provider credentials to the login Keychain. On other
platforms it uses an installed system `keyring` backend when available. If no
secure writable backend exists, the fallback is read-only environment
variables with this form:

```text
OIDA_REASONING_<PROVIDER_ID>_<CREDENTIAL_NAME>
```

For example, the default OpenRouter key name resolves to
`OIDA_REASONING_OPENROUTER_API_KEY`. Credential values are excluded from
settings responses, logs, URLs, and command arguments.

## Host-managed And Daemon-managed Turns

`oida_ask` and `POST /conversation/ask` are daemon-managed. Oída selects the
configured conversation provider, performs validation and any allowed local
re-listen, and commits the final turn.

An already active host should avoid asking Oída to launch the same host CLI
recursively. The split flow is intended for this case:

1. `oida_prepare_turn` or `POST /conversation/prepare` returns Oída's system
   prompt, user prompt, evidence packet, response schema, and a short-lived
   single-use commit token.
2. The active host reasons with that exact envelope and no tools.
3. `oida_commit_turn` or `POST /conversation/commit` submits the structured
   response. Oída validates it, applies the one-re-listen rule if requested,
   and stores only a valid final turn.

A failed or abandoned preparation creates no empty conversation. Commit tokens
expire and cannot be replayed.

## API Discovery

The dashboard uses the same loopback API available to other local clients:

- `GET /reasoning/providers` and `GET /reasoning/models?provider_id=...`;
- `GET` and `PUT /reasoning/settings`;
- provider probe, credential, and OpenRouter authorization routes under
  `/reasoning/*`;
- `POST /conversation/ask` and `POST /conversation/ask/stream`;
- `POST /conversation/prepare` and `POST /conversation/commit`;
- `GET /conversation?event_id=...`, `GET /conversation/{id}`, and `DELETE
  /conversation/{id}`.

These controls are loopback administration surfaces. A wildcard/LAN daemon
still requires Oída's bearer-token protection, and enabling a provider does not
weaken the evidence-packet rules.
