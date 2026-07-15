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
OpenMOSS team. The official
[MOSS-Audio-4B-Instruct model card](https://huggingface.co/OpenMOSS-Team/MOSS-Audio-4B-Instruct)
identifies Apache-2.0 terms for that release.

Oída does not redistribute MOSS-Audio code or weights and does not download
them silently. Install the exact upstream release yourself, read its model
card and license at the time of use, and record the chosen model identifier in
research or publication metadata. Upstream terms remain authoritative if they
change or differ between checkpoints.

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
