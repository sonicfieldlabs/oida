# Models and Licensing

Oída is usable without an audio model. The `stub` profile performs
deterministic signal analysis and is the recommended first run. Model code,
weights, hosted services, and host logins are optional operator choices and
are not bundled in this repository.

## Repository License

Oída source code is licensed under Apache-2.0. The license covers this
repository's code; it does not grant rights to third-party models, datasets,
recordings, provider services, or generated material.

## MOSS-Audio

The embedded `mac-mps` and external `cuda-server` paths integrate with
[MOSS-Audio](https://github.com/OpenMOSS/MOSS-Audio), developed by the
OpenMOSS team. Oída's current embedded path is developed and tested first with
the four released MOSS-Audio checkpoints:

- [MOSS-Audio 4B Instruct](https://huggingface.co/OpenMOSS-Team/MOSS-Audio-4B-Instruct)
- [MOSS-Audio 4B Thinking](https://huggingface.co/OpenMOSS-Team/MOSS-Audio-4B-Thinking)
- [MOSS-Audio 8B Instruct](https://huggingface.co/OpenMOSS-Team/MOSS-Audio-8B-Instruct)
- [MOSS-Audio 8B Thinking](https://huggingface.co/OpenMOSS-Team/MOSS-Audio-8B-Thinking)

The official repository and these model cards identify Apache-2.0 terms for
the released code and checkpoints. The Instruct variants are used for direct
listening and transcription routes. Thinking variants support deeper,
music-focused, and targeted re-listening routes. Oída consumes their final
response as bounded evidence; it does not expose private reasoning traces.

The 4B pair is the recommended local starting point. Oída's public-alpha
planning guidance is 16 GB minimum and 24 GB suggested for 4B, or 24 GB
minimum and 48 GB suggested for 8B. Actual memory and speed vary with device,
audio duration, precision, and resident-model policy.

Oída does not redistribute MOSS-Audio code or weights and does not download
them silently. Install the exact upstream release yourself, read its model
card and license at the time of use, and record the chosen model identifier in
research or publication metadata. Upstream terms remain authoritative if they
change or differ between checkpoints.

See [MOSS-Audio setup](model-setup.md) for the exact manual downloads and the
[Listening Stack installer](https://github.com/sonicfieldlabs/listening-stack)
for the guided route. The installer keeps weights outside Git and records
local paths without placing credentials in repository files.

## Other Local, Hosted, and Host Models

The dashboard catalog and gateway can connect to operator-managed local audio
or reasoning endpoints, Ollama, OpenAI-compatible services, Google Gemini,
Alibaba Qwen, NVIDIA NIM, OpenRouter, and supported host CLIs. A catalog entry
is an integration description, not a bundled model or license grant.

For every enabled provider:

- review the selected model card, code license, weight license, service terms,
  geographic availability, and usage restrictions;
- disclose the exact model and provider when reporting results;
- understand whether derived evidence, transcripts, or raw audio leave the
  machine;
- do not assume that one provider's terms cover another checkpoint, adapter,
  LoRA, dataset, or generated asset.

Host CLI adapters use the operator's existing installation and login. Oída
does not own those accounts or terms. External audio transfer is separately
permissioned and disabled by default.

## Audio and Research Outputs

Oída does not determine ownership of input recordings, listening notes,
model-generated text, or downstream audio. Contributors and operators are
responsible for consent, provenance, attribution, and any rights required for
collection, analysis, storage, export, and publication.

When citing this software, use [CITATION.cff](../CITATION.cff). When a model
contributed to a result, cite the exact upstream model release separately.
